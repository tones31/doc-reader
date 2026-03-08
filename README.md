# Resume Search (doc-reader)

A **resume search** app that lets you upload PDF/text resumes, index them with embeddings, and ask natural-language questions to find the best candidates. Built with FastAPI, Streamlit, ChromaDB, and OpenAI.

## Features

- **Upload resumes** — PDF or plain text; files are chunked, embedded, and stored in ChromaDB
- **Ask questions** — e.g. *"Who has the most Python experience?"* or *"Find candidates who know DevOps"*; answers are grounded in your documents with ranked candidates
- **Document library** — List and download uploaded resumes (paginated)
- **Storage** — Local disk by default, or S3-compatible storage when configured
- **Reset** — Wipe the vector DB from the Settings tab when you need a fresh start

## Architecture

- **Backend** (`main.py`) — FastAPI app: ingest PDF/text, embed with OpenAI `text-embedding-3-small`, store in ChromaDB; `/ask` does retrieval + GPT-4.1-mini for answers and ranked candidates
- **Frontend** (`ui.py`) — Streamlit app: Ask tab, Resumes tab (upload + list + download), Settings (danger zone)
- **Storage** (`storage.py`) — S3 when bucket + credentials are set; otherwise uses a local directory

## Requirements

- Python 3.10+
- OpenAI API key

## Setup

1. **Clone and create a virtual environment**

   ```bash
   git clone <repo-url>
   cd doc-reader
   python -m venv .venv
   source .venv/bin/activate   # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Environment variables**

   Create a `.env` in the project root:

   | Variable | Description |
   |----------|-------------|
   | `OPEN_API_KEY` | OpenAI API key (embeddings + chat) |
   | `API_URL` | Backend URL for the Streamlit app (e.g. `http://localhost:8000`) |
   | `FRONTEND_URL` | Streamlit app URL for CORS (e.g. `http://localhost:8501`) |
   | `DOCUMENT_LOCAL_DIR` | Local folder for uploaded files when not using S3 (e.g. `document_storage`) |

   **Optional (S3-compatible storage):**

   | Variable | Description |
   |----------|-------------|
   | `DOCUMENT_BUCKET` | Bucket name |
   | `DOCUMENT_BUCKET_ACCESS_KEY_ID` | Access key |
   | `DOCUMENT_BUCKET_SECRET_ACCESS_KEY` | Secret key |
   | `DOCUMENT_BUCKET_REGION` | Region (e.g. `us-east-1`) |
   | `DOCUMENT_BUCKET_ENDPOINT` | Custom endpoint URL (optional) |

3. **Run locally**

   **Terminal 1 — Backend:**

   ```bash
   uvicorn main:app --reload
   ```

   **Terminal 2 — Frontend:**

   ```bash
   streamlit run ui.py
   ```

   - API: http://localhost:8000  
   - UI: http://localhost:8501  

   Set `API_URL=http://localhost:8000` and `FRONTEND_URL=http://localhost:8501` in `.env` so the UI can call the API and CORS allows the origin.

## API overview

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Health / status |
| POST | `/ingest` | Ingest raw text (`{"text": "..."}`) |
| POST | `/ingest_pdf` | Ingest PDF (multipart `file`) |
| POST | `/ask` | RAG Q&A (`{"question": "..."}`) → answer + ranked candidates + excerpts |
| GET | `/documents/list` | List stored document names |
| GET | `/documents/download?filename=...` | Download file (redirect to S3 presigned URL or file response) |
| POST | `/wipe` | Delete all documents from the ChromaDB collection |

## Deployment (Railway)

The repo includes `railway.toml` defining two services:

- **backend** — `uvicorn main:app --host 0.0.0.0 --port $PORT`
- **frontend** — `streamlit run ui.py --server.port $PORT --server.address 0.0.0.0`

Set in Railway:

- `OPEN_API_KEY`
- `API_URL` → public URL of the backend service
- `FRONTEND_URL` → public URL of the Streamlit service (no trailing slash)
- `DOCUMENT_LOCAL_DIR` or the S3 variables if you use object storage

For production, add a `/health` route if your platform expects it (e.g. `healthcheckPath = "/health"` in `railway.toml`).

## License

See repository license.
