import os
import chromadb
import uuid
from fastapi import FastAPI, File, UploadFile
from pydantic import BaseModel
from openai import OpenAI
from dotenv import load_dotenv
from chromadb.utils import embedding_functions
from pypdf import PdfReader

load_dotenv()

# Models

class DocumentRequest(BaseModel):
    text: str

class SearchRequest(BaseModel):
    query: str

class QuestionRequest(BaseModel):
    question: str

# Constants

openai_api_key = os.getenv("OPEN_API_KEY")
app = FastAPI()
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

def create_question(question: str, context: str):
    return f"Use the following context to answer the question:\n\n{context}\n\nQuestion: {question}"

def extract_text_from_pdf(pdf_path: str):
    reader = PdfReader(pdf_path)
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

@app.post("/ask")
def ask_question(request: QuestionRequest):
    # Embed and retrieve top 3 relevant documents
    results = collection.query(
        query_texts=[request.question],
        n_results=3
    )

    # Combine retrieved documents into a single context string
    retrieved_documents = results["documents"][0]
    context = "\n\n".join(retrieved_documents)

    # Send context + question to LLM
    question = create_question(request.question, context)
    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": "You are a helpful assistant. ONLY answer using the provided context. Do NOT use any outside knowledge. If the context does not contain the answer, say 'I don't know'."},
            {"role": "user", "content": question}
        ]
    )

    answer = response.choices[0].message.content
    
    return {
        "answer": answer,
        "question": request.question,
        "retrieved_documents": retrieved_documents
    }

@app.post("/ingest")
def ingest_document(request: DocumentRequest):
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
def ingest_pdf(file: UploadFile = File(...)):
    text = extract_text_from_pdf(file.file)
    
    doc_id = str(uuid.uuid4())
    chunks = chunk_text(text)
    
    collection.add(
        documents=chunks,
        metadatas=[{"id": doc_id}] * len(chunks),
        ids=[str(uuid.uuid4()) for _ in chunks]
    )

    return {
        "status": "stored",
        "id": doc_id
    }
    

@app.post("/search")
def search(request: SearchRequest):
    query_embeddings = embedding_function.embed_query(request.query)
    results = collection.query(
        query_embeddings=query_embeddings,
        n_results=5
    )

    scores = {}
    for i, metadata_list in enumerate(results["metadatas"][0]):
        id = metadata_list["id"]
        scores[id] = scores.get(id, 0) + 1
    
    top_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)

    return {
        "query": request.query,
        "top_scores": top_scores
    }
