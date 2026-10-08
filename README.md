# Kenya Airways Customer Support RAG Chatbot

**Project title:** A RAG-Based Kenyan Multilingual Virtual Assistant for Automated Airline Customer Support.

An academic web-based customer-support assistant designed to provide accessible, document-grounded information about Kenya Airways (KQ) services and policies in **English, Kiswahili, and mixed English-Kiswahili**.

The solution combines semantic document retrieval with generative AI to deliver relevant answers supported by source references. Its purpose is to improve access to airline information while supporting transparent response evaluation and knowledge-base administration.

The overview below presents the intended final system; the setup section covers backend installation and configuration.

## Key features

- **Multilingual customer support:** English, Kiswahili, and mixed-language interactions through a web-based chat interface.
- **Document-grounded answers:** Retrieval-Augmented Generation (RAG) uses relevant KQ policy content as evidence for responses.
- **Source transparency:** Citations and retrieval records connect answers to their supporting documents.
- **Responsible fallback handling:** Questions with insufficient supporting information receive clarification or an appropriate support response.
- **Conversation management:** Authenticated passengers can start conversations, revisit previous messages, and end sessions.
- **Passenger feedback:** Response ratings and comments support quality monitoring.
- **Knowledge-base administration:** Administrators can upload, update, remove, and process support documents.
- **Role-based access:** Separate passenger and administrator permissions protect application functions.
- **Operational analytics:** Dashboard summaries cover usage, feedback, response times, and system activity.
- **AI model comparison:** Three generation models are evaluated using consistent questions, evidence, and scoring criteria.

## Customer-support coverage

| Area | Information covered |
|---|---|
| Booking | Booking guidance and related policies |
| Baggage | Allowances, restrictions, and delayed, lost, or damaged baggage |
| Check-in | Procedures and requirements |
| Refunds and payments | Refund guidance and payment information |
| Upgrades | Upgrade information and applicable conditions |
| Travel requirements | Travel documentation and policy guidance |
| General support | Frequently asked questions, contact information, and customer-care policies |

The application is an informational customer-support system rather than a transaction-processing or live reservation platform.

## Users

| User | Capabilities |
|---|---|
| Passenger | Register and log in, ask support questions, view and end conversations, and provide feedback. |
| Administrator | Log in, manage and process documents, manage users, inspect conversations, view analytics, and manage/evaluate AI models. |

## Technology stack

| Layer | Technologies | Purpose |
|---|---|---|
| Frontend | React | Passenger chat interface and administrator dashboard |
| Backend | Python, FastAPI, Uvicorn | API endpoints and application services |
| Validation and configuration | Pydantic, python-dotenv | Validated data models and environment-based settings |
| Authentication | Firebase Authentication, Firebase Admin SDK | User identity and role-based authorization |
| Application database | Firestore | Users, conversations, messages, feedback, and administrative records |
| Vector database | ChromaDB | Document embeddings and semantic retrieval |
| RAG orchestration | LangChain | Retrieval, context preparation, and generation workflow |
| Document processing | python-docx, pypdf | Word content, tables, and selectable-text PDF extraction |
| Embeddings | Multilingual embedding model | Semantic representations of documents and questions |
| Answer generation | Generative AI APIs | Evidence-based responses and comparative model evaluation |

Backend package versions are listed in [requirements.txt](backend/requirements.txt).

## System workflow

```text
KQ documents -> Text extraction -> Chunking -> Embeddings -> ChromaDB
                                                               |
Passenger question -> Language processing -> Semantic retrieval
                                                  |
                                          Context preparation
                                                  |
                                          Selected AI model
                                                  |
                                     Answer and source references
                                                  |
                                      Conversation and feedback
```

Document preparation preserves metadata for source traceability. Query processing identifies relevant evidence, and context preparation selects the information supplied to the generation model. When evidence is insufficient, the response follows the fallback or clarification path.

## Evaluation

The evaluation framework compares three generation models under consistent retrieval and prompting conditions. Results are assessed separately for English, Kiswahili, and mixed-language questions.

| Measure | Evaluation focus |
|---|---|
| Answer correctness | Agreement with verified reference answers |
| Faithfulness | Support for generated claims in the supplied context |
| Answer relevance | How directly the response addresses the question |
| Context precision and recall | Relevance and coverage of retrieved evidence |
| Fallback behaviour | Appropriate handling of unanswerable questions |
| Language quality | Clarity and suitability of the response language |
| Response time and reliability | Latency and request failures |

Retrieval quality is evaluated separately from generation quality. Fixed retrieved evidence enables a fair comparison of model responses.

## Project structure

```text
backend/
  app/
    api/          API routes and authentication dependencies
    models/       Validated application data models
    rag/          Document loading and chunking
    services/     Firebase authentication services
    utils/        Shared utilities
    config.py     Application settings
    main.py       FastAPI application
  data/
    raw/          Original knowledge-base documents
    processed/    Exported document chunks
  scripts/        Command-line ingestion
  requirements.txt
```

## Backend setup

The following commands use Windows PowerShell to prepare the backend environment and configuration.

### 1. Prerequisites and clone

Prerequisites: Git and Python 3.12 available in the terminal.

```powershell
git clone https://github.com/GlenSikuku9/KQ-RAG-Chatbot.git
Set-Location .\KQ-RAG-Chatbot
```

### 2. Create the backend environment and install dependencies

Run from the repository root:

```powershell
Set-Location .\backend
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r .\requirements.txt
```

Commands use the virtual environment's Python directly; shell activation is optional.

### 3. Create local configuration

Still inside the backend directory:

```powershell
if (-not (Test-Path .\.env)) {
    Copy-Item .\.env.example .\.env
}
```

Application settings are loaded from the local environment file. The copy command preserves any existing configuration.

| Setting | Purpose |
|---|---|
| `APP_NAME`, `APP_VERSION`, `ENVIRONMENT`, `DEBUG` | Application identity and development settings |
| `API_PREFIX` | API route prefix; defaults to `/api/v1` |
| `CORS_ORIGINS` | Allowed browser origins; local ports 5173 and 3000 are configured by default |
| `FIRESTORE_PROJECT_ID` | Firebase project ID used during Admin SDK initialization |
| `FIREBASE_CREDENTIALS_PATH` | Optional path to Firebase service-account credentials |
| `RAW_DATA_DIR`, `PROCESSED_DATA_DIR` | Input documents and exported chunk locations |
| `CHROMA_PERSIST_DIR` | Vector storage directory |
| `RAG_CHUNK_SIZE`, `RAG_CHUNK_OVERLAP` | Character-based chunk size and overlap; defaults are 1000 and 150 |
| `EMBEDDING_MODEL_NAME`, `DEFAULT_GENERATION_MODEL_ID` | Embedding and generation model selection |

Relative filesystem settings are resolved from the backend directory. Model selection is not required for backend startup or document extraction.
