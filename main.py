import io
import os
import re
import chromadb
import uuid
from pathlib import Path
from fastapi import FastAPI, File, UploadFile, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi import HTTPException
from pydantic import BaseModel
from openai import OpenAI
from dotenv import load_dotenv
from chromadb.utils import embedding_functions
from pypdf import PdfReader
import storage as storage_module
import auth

load_dotenv()

# Models

class DocumentRequest(BaseModel):
    text: str

class QuestionRequest(BaseModel):
    question: str

# Constants (storage uses S3 when BUCKET + credentials set; else local UPLOAD_DIR)

openai_api_key = os.getenv("OPEN_API_KEY")
app = FastAPI()

# CORS: allow frontend origin (set FRONTEND_URL on Railway to your Streamlit service URL)
frontend_url = os.getenv("FRONTEND_URL").rstrip("/")
allow_origins = [frontend_url]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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

def safe_filename(name: str) -> str:
    """Return a safe basename for storage; prevents path traversal."""
    base = os.path.basename(name)
    base = base.replace("..", "").replace("/", "").replace("\\", "")
    base = re.sub(r"[^\w\s.\-]", "", base).strip()
    return base or "document"

def create_question(question: str, context: str):
    return f"Use the following context to answer the question:\n\n{context}\n\nQuestion: {question}"

def extract_text_from_pdf(source):
    """Accept a file path (str) or file-like object (e.g. BytesIO)."""
    reader = PdfReader(source)
    text = ""
    for page in reader.pages:
        text += page.extract_text() + "\n"
    return text

def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50):
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += chunk_size - overlap
    return chunks

# Routes

@app.get("/")
def root():
    return {"message": "AI server is running"}


# --- Google OAuth + JWT (when GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, JWT_SECRET set) ---

@app.get("/auth/google")
def auth_google(request: Request):
    """Redirect to Google consent screen. Callback is /auth/google/callback."""
    if not auth.auth_enabled():
        raise HTTPException(status_code=501, detail="Google SSO not configured")
    redirect_uri = str(request.base_url).rstrip("/") + "/auth/google/callback"
    url, _state = auth.build_google_auth_url(redirect_uri)
    return RedirectResponse(url=url, status_code=302)


@app.get("/auth/google/callback")
def auth_google_callback(request: Request, code: str | None = None, state: str | None = None):
    """Exchange code for user, create JWT, redirect to frontend with ?token=..."""
    if not auth.auth_enabled():
        raise HTTPException(status_code=501, detail="Google SSO not configured")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")
    redirect_uri = str(request.base_url).rstrip("/") + "/auth/google/callback"
    user = auth.exchange_code_for_user(code, state, redirect_uri)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired login")
    token = auth.create_session_token(user)
    return RedirectResponse(url=f"{frontend_url}?token={token}", status_code=302)


@app.get("/auth/me")
def auth_me(user: dict | None = Depends(auth.get_current_user_optional)):
    """Return current user email/name when authenticated. 401 when auth is on and not logged in."""
    if not auth.auth_enabled():
        return {"email": None, "name": None}
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {"email": user.get("email"), "name": user.get("name")}


# --- Protected API (require JWT when auth enabled) ---

@app.post("/wipe")
def wipe_collection(_user: dict | None = Depends(auth.get_current_user_optional)):
    """Remove all documents from the ChromaDB collection."""
    result = collection.get(include=[])
    ids = result["ids"] if result["ids"] else []
    if ids:
        collection.delete(ids=ids)
    return {"status": "wiped", "deleted_count": len(ids)}


@app.post("/ask")
def ask_question(request: QuestionRequest, _user: dict | None = Depends(auth.get_current_user_optional)):
    # Retrieve more chunks so multiple candidates can be represented
    results = collection.query(
        query_texts=[request.question],
        n_results=15,
        include=["metadatas", "documents", "distances"]
    )

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

@app.post("/ingest")
def ingest_document(request: DocumentRequest, _user: dict | None = Depends(auth.get_current_user_optional)):
    doc_id = str(uuid.uuid4())

    collection.add(
        documents=[request.text],
        ids=[doc_id]
    )

    return {
        "status": "stored",
        "id": doc_id
    }

@app.post("/ingest_pdf")
def ingest_pdf(file: UploadFile = File(...), _user: dict | None = Depends(auth.get_current_user_optional)):
    # Overwrite by name: remove existing chunks with same filename
    try:
        collection.delete(where={"filename": file.filename})
    except Exception:
        pass  # No existing docs with this filename

    content = file.file.read()
    safe = safe_filename(file.filename)
    storage_module.save_file(safe, content)

    text = extract_text_from_pdf(io.BytesIO(content))
    doc_id = str(uuid.uuid4())
    chunks = chunk_text(text)

    collection.add(
        documents=chunks,
        metadatas=[{"id": doc_id, "filename": file.filename}] * len(chunks),
        ids=[str(uuid.uuid4()) for _ in chunks]
    )

    return {
        "status": "stored",
        "id": doc_id
    }


@app.get("/documents/list")
def list_documents(_user: dict | None = Depends(auth.get_current_user_optional)):
    """Return list of stored document names for UI table and download links."""
    return {"documents": storage_module.list_files()}


@app.get("/documents/download")
def download_document(request: Request, filename: str):
    # Require auth when enabled; allow token in query for download links (browser can't send Bearer on link click)
    if auth.auth_enabled():
        user = None
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            user = auth.decode_session_token(auth_header[7:].strip())
        if not user:
            token = request.query_params.get("token")
            if token:
                user = auth.decode_session_token(token)
        if not user:
            raise HTTPException(status_code=401, detail="Not authenticated")
    safe = safe_filename(filename)
    if storage_module.is_s3():
        url = storage_module.get_presigned_url(safe)
        if not url:
            raise HTTPException(status_code=404, detail="Document not found")
        return RedirectResponse(url=url, status_code=302)
    path = storage_module.get_file_path(safe)
    if not path:
        raise HTTPException(status_code=404, detail="Document not found")
    return FileResponse(path, filename=filename, media_type="application/pdf")
