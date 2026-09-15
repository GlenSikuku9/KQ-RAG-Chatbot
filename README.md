# Kenya Airways Customer Support RAG Chatbot

A customer-support chatbot project for Kenya Airways using a FastAPI backend, document-based retrieval, and a frontend application.

## Project structure

- `backend/` – FastAPI API, authentication, document ingestion, and RAG utilities
- `backend/data/raw/` – source customer-support documents
- `backend/data/processed/` – processed document chunks
- `docs/` – project and agent instructions
- `frontend/` – frontend application files

## Backend setup

```powershell
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

The local `.env`, virtual environment, and generated `chroma_db/` directory are intentionally excluded from version control.
