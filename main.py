import io
import os
import re
import time
import logging
import chromadb
import uuid
from pathlib import Path

# Configure logging before importing auth so auth's startup log is visible
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

from fastapi import FastAPI, File, UploadFile, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel
from openai import OpenAI
from dotenv import load_dotenv
from chromadb.utils import embedding_functions
from pypdf import PdfReader
import storage as storage_module
import auth

load_dotenv()

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every request: method, path, status code, duration. WARN for 4xx/5xx."""

    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        method = request.method
        path = request.url.path
        try:
            response = await call_next(request)
            status = response.status_code
            duration_ms = (time.perf_counter() - start) * 1000
            if status >= 400:
                logger.warning(
                    "%s %s -> %s %.0fms",
                    method,
                    path,
                    status,
                    duration_ms,
                )
            else:
                logger.info(
                    "%s %s -> %s %.0fms",
                    method,
                    path,
                    status,
                    duration_ms,
                )
            return response
        except HTTPException:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.warning(
                "%s %s -> HTTPException after %.0fms",
                method,
                path,
                duration_ms,
            )
            raise
        except Exception as e:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.error(
                "%s %s -> exception after %.0fms: %s",
                method,
                path,
                duration_ms,
                e,
                exc_info=True,
            )
            raise


# Models

class DocumentRequest(BaseModel):
    text: str

class QuestionRequest(BaseModel):
    question: str

# Constants (storage uses S3 when BUCKET + credentials set; else local UPLOAD_DIR)

openai_api_key = os.getenv("OPEN_API_KEY")
# Backend public URL for OAuth redirect_uri (API_URL may omit protocol; we default to https)
_api_url = (os.getenv("API_URL") or "").strip().rstrip("/")
if _api_url and not _api_url.startswith(("http://", "https://")):
    _api_url = "https://" + _api_url
backend_base_url = _api_url or None  # None => use request.base_url in handlers

app = FastAPI()

# Request logging (method, path, status, duration); WARN for 4xx/5xx
app.add_middleware(RequestLoggingMiddleware)

# CORS: allow frontend origin
frontend_url = os.getenv("FRONTEND_URL").rstrip("/")
allow_origins = [frontend_url]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HTTPException)
def http_exception_handler(request: Request, exc: HTTPException):
    logger.warning(
        "HTTPException %s %s -> %s %s",
        request.method,
        request.url.path,
        exc.status_code,
        exc.detail,
    )
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(RequestValidationError)
def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.warning(
        "Validation error %s %s: %s",
        request.method,
        request.url.path,
        exc.errors(),
    )
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.exception_handler(Exception)
def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error(
        "Unhandled exception %s %s: %s",
        request.method,
        request.url.path,
        exc,
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


client = OpenAI(api_key=openai_api_key)
chroma_client = chromadb.PersistentClient(path="chroma_db")

embedding_function = embedding_functions.OpenAIEmbeddingFunction(
    api_key=openai_api_key,
    model_name="text-embedding-3-small"
)

collection = chroma_client.get_or_create_collection(
    name="documents",
    embedding_function=embedding_function
)

# Functions

# Returns a safe basename for storage; prevents path traversal.
def safe_filename(name: str) -> str:
    base = os.path.basename(name)
    base = base.replace("..", "").replace("/", "").replace("\\", "")
    base = re.sub(r"[^\w\s.\-]", "", base).strip()
    return base or "document"

# Builds a prompt string: context plus question for the LLM.
def create_question(question: str, context: str):
    return f"Use the following context to answer the question:\n\n{context}\n\nQuestion: {question}"

# Extracts full text from a PDF. Accepts a file path (str) or file-like object (e.g. BytesIO).
def extract_text_from_pdf(source):
    reader = PdfReader(source)
    text = ""
    for page in reader.pages:
        text += page.extract_text() + "\n"
    return text

# Splits text into overlapping chunks of chunk_size with overlap characters between chunks.
def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50):
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += chunk_size - overlap
    return chunks


# Returns sanitized Google sub for storage/Chroma scoping, or None when auth disabled.
def get_user_id(user: dict | None) -> str | None:
    if not user:
        return None
    raw = (user.get("sub") or "").replace("/", "").replace("..", "")
    return raw if raw else None


# Returns the base URL to use for OAuth redirect_uri: API_URL if set (with https default),
# else https when behind a proxy (X-Forwarded-Proto), else request.base_url for local dev.
def oauth_redirect_base(request: Request) -> str:
    if backend_base_url:
        return backend_base_url
    proto = request.headers.get("X-Forwarded-Proto", "").strip().lower()
    host = request.headers.get("X-Forwarded-Host", "").strip() or request.url.hostname
    if proto == "https" and host:
        return f"https://{host}"
    return str(request.base_url).rstrip("/")


# Routes

# Health check: returns a simple message that the AI server is running.
@app.get("/")
def root():
    return {"message": "AI server is running"}


# --- Google OAuth + JWT (when GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, JWT_SECRET set) ---

# Redirects to Google OAuth consent screen. Callback is /auth/google/callback.
@app.get("/auth/google")
def auth_google(request: Request):
    if not auth.auth_enabled():
        raise HTTPException(status_code=501, detail="Google SSO not configured")
    base = oauth_redirect_base(request)
    redirect_uri = base + "/auth/google/callback"
    logger.info("OAuth redirect_uri: %s", redirect_uri)
    url, _state = auth.build_google_auth_url(redirect_uri)
    return RedirectResponse(url=url, status_code=302)


# Exchanges code for user, creates JWT, redirects to frontend with ?token=...
@app.get("/auth/google/callback")
def auth_google_callback(request: Request, code: str | None = None, state: str | None = None):
    if not auth.auth_enabled():
        raise HTTPException(status_code=501, detail="Google SSO not configured")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")
    base = oauth_redirect_base(request)
    redirect_uri = base + "/auth/google/callback"
    user = auth.exchange_code_for_user(code, state, redirect_uri)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired login")
    token = auth.create_session_token(user)
    return RedirectResponse(url=f"{frontend_url}?token={token}", status_code=302)


# Returns current user email/name when authenticated. 401 when auth is on and not logged in.
@app.get("/auth/me")
def auth_me(user: dict | None = Depends(auth.get_current_user_optional)):
    enabled = auth.auth_enabled()
    if not enabled:
        logger.info("/auth/me: auth disabled, returning 200 (no login required)")
        return {"email": None, "name": None}
    if not user:
        logger.info("/auth/me: auth enabled, no token/invalid -> 401 (show login)")
        raise HTTPException(status_code=401, detail="Not authenticated")
    logger.debug("/auth/me: authenticated as %s", user.get("email"))
    return {"email": user.get("email"), "name": user.get("name"), "picture": user.get("picture")}


# --- Protected API (require JWT when auth enabled) ---

# Removes all documents from the ChromaDB collection (when auth on: only current user's).
@app.post("/wipe")
def wipe_collection(user: dict | None = Depends(auth.get_current_user_optional)):
    user_id = get_user_id(user)
    if user_id is not None:
        result = collection.get(where={"user_id": user_id}, include=[])
    else:
        result = collection.get(include=[])
    ids = result["ids"] if result["ids"] else []
    if ids:
        collection.delete(ids=ids)
    return {"status": "wiped", "deleted_count": len(ids)}


# Queries ChromaDB for relevant chunks, ranks candidates, and returns LLM answer with ranked_candidates and excerpts.
@app.post("/ask")
def ask_question(request: QuestionRequest, user: dict | None = Depends(auth.get_current_user_optional)):
    # Retrieve more chunks so multiple candidates can be represented
    user_id = get_user_id(user)
    query_kwargs = dict(
        query_texts=[request.question],
        n_results=15,
        include=["metadatas", "documents", "distances"],
    )
    if user_id is not None:
        query_kwargs["where"] = {"user_id": user_id}
    results = collection.query(**query_kwargs)

    retrieved_documents = results["documents"][0]
    metadatas = results["metadatas"][0]
    chunk_ids = results["ids"][0]
    distances = results.get("distances")
    dist_list = distances[0] if distances else None

    # Rank by document: use best-chunk relevance (1 / (1 + min_distance)) or fallback to chunk count
    scores = {}
    filenames = {}
    if dist_list is not None and len(dist_list) == len(metadatas):
        for i, metadata_list in enumerate(metadatas):
            meta = metadata_list or {}
            doc_id = meta.get("id") or chunk_ids[i]
            d = dist_list[i]
            if doc_id not in scores or d < scores[doc_id]:
                scores[doc_id] = d  # store min distance per doc (will convert to relevance)
            if "filename" in meta:
                filenames[doc_id] = meta["filename"]
        # Convert min distance to relevance (higher = better), sort descending
        ranked_candidates = [
            {"id": doc_id, "score": round(1 / (1 + min_dist), 2), **({"filename": filenames[doc_id]} if doc_id in filenames else {})}
            for doc_id, min_dist in sorted(scores.items(), key=lambda x: 1 / (1 + x[1]), reverse=True)
        ]
    else:
        for i, metadata_list in enumerate(metadatas):
            meta = metadata_list or {}
            doc_id = meta.get("id") or chunk_ids[i]
            scores[doc_id] = scores.get(doc_id, 0) + 1
            if "filename" in meta:
                filenames[doc_id] = meta["filename"]
        ranked_candidates = [
            {"id": doc_id, "score": score, **({"filename": filenames[doc_id]} if doc_id in filenames else {})}
            for doc_id, score in sorted(scores.items(), key=lambda x: x[1], reverse=True)
        ]

    # Build context with doc id prefix so the model can refer to candidates
    context_parts = []
    for i, doc in enumerate(retrieved_documents):
        meta = metadatas[i] or {}
        doc_id = meta.get("id") or chunk_ids[i]
        name = filenames.get(doc_id, doc_id)
        context_parts.append(f"[Candidate: {name}]\n{doc}")
    context = "\n\n".join(context_parts)

    # LLM with candidate-focused prompt
    prompt = create_question(request.question, context)
    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": "You are a recruiter assistant. Answer the question using ONLY the provided resume excerpts. Say which candidate (by name or id) is best and why. If the context does not contain enough information, say so. Do NOT use any outside knowledge."},
            {"role": "user", "content": prompt}
        ]
    )

    answer = response.choices[0].message.content

    return {
        "answer": answer,
        "question": request.question,
        "ranked_candidates": ranked_candidates,
        "retrieved_documents": retrieved_documents
    }

# Stores raw text as a single document in ChromaDB; returns status and document id.
@app.post("/ingest")
def ingest_document(request: DocumentRequest, user: dict | None = Depends(auth.get_current_user_optional)):
    doc_id = str(uuid.uuid4())
    user_id = get_user_id(user)
    add_kwargs = dict(documents=[request.text], ids=[doc_id])
    if user_id is not None:
        add_kwargs["metadatas"] = [{"user_id": user_id}]
    collection.add(**add_kwargs)
    return {
        "status": "stored",
        "id": doc_id
    }

# Uploads PDF to storage, extracts text, chunks it, and adds chunks to ChromaDB. Overwrites by filename (and user when auth on).
@app.post("/ingest_pdf")
def ingest_pdf(file: UploadFile = File(...), user: dict | None = Depends(auth.get_current_user_optional)):
    user_id = get_user_id(user)
    # Overwrite by name (and user when auth on): remove existing chunks with same filename
    try:
        if user_id is not None:
            collection.delete(where={"$and": [{"filename": file.filename}, {"user_id": user_id}]})
        else:
            collection.delete(where={"filename": file.filename})
    except Exception:
        pass

    content = file.file.read()
    safe = safe_filename(file.filename)
    storage_key = f"{user_id}/{safe}" if user_id else safe
    storage_module.save_file(storage_key, content)

    text = extract_text_from_pdf(io.BytesIO(content))
    doc_id = str(uuid.uuid4())
    chunks = chunk_text(text)

    meta_base = {"id": doc_id, "filename": file.filename}
    if user_id is not None:
        meta_base["user_id"] = user_id
    collection.add(
        documents=chunks,
        metadatas=[meta_base] * len(chunks),
        ids=[str(uuid.uuid4()) for _ in chunks]
    )
    return {
        "status": "stored",
        "id": doc_id
    }


# Returns list of stored document names for UI table and download links.
@app.get("/documents/list")
def list_documents(user: dict | None = Depends(auth.get_current_user_optional)):
    user_id = get_user_id(user)
    keys = storage_module.list_files(user_id=user_id)
    return {"documents": [{"name": os.path.basename(k)} for k in keys]}


# Serves document download. Requires auth when enabled; token may be in query for browser links (no Bearer on link click).
@app.get("/documents/download")
def download_document(request: Request, filename: str):
    user = None
    if auth.auth_enabled():
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            user = auth.decode_session_token(auth_header[7:].strip())
        if not user:
            token = request.query_params.get("token")
            if token:
                user = auth.decode_session_token(token)
        if not user:
            raise HTTPException(status_code=401, detail="Not authenticated")
    user_id = get_user_id(user)
    safe = safe_filename(filename)
    key = f"{user_id}/{safe}" if user_id else safe
    if user_id and not key.startswith(user_id + "/"):
        raise HTTPException(status_code=403, detail="Forbidden")
    if storage_module.is_s3():
        url = storage_module.get_presigned_url(key)
        if not url:
            raise HTTPException(status_code=404, detail="Document not found")
        return RedirectResponse(url=url, status_code=302)
    path = storage_module.get_file_path(key)
    if not path:
        raise HTTPException(status_code=404, detail="Document not found")
    return FileResponse(path, filename=filename, media_type="application/pdf")
