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
| Document processing | python-docx | Editable DOCX paragraphs and tables |
| Embeddings | Multilingual embedding model | Semantic representations of documents and questions |
| Answer generation | Generative AI APIs | Evidence-based responses and comparative model evaluation |

Backend package versions are listed in [requirements.txt](backend/requirements.txt).

The knowledge base accepts DOCX documents containing editable text and tables. Extraction preserves row/column positions, empty cells, standard merged cells, nested tables, and declared headers without guessing header meanings. Bullets and simple decimal/alphabetical lists retain their labels and nesting. Headers, footers, and notes are included as labelled supplementary content.

Unsupported or ambiguous content produces review warnings. Flagged versions retain their extracted text in the offline report but cannot be published; an existing successful version stays active. Empty, unreadable, and image-only documents fail explicitly; unsupported file formats are reported and skipped.

Chunking uses configurable character targets (`RAG_CHUNK_SIZE`, `RAG_CHUNK_OVERLAP`), not hard limits. Table rows stay intact with repeated declared headers or explicitly labelled opening-row context; merged/nested tables stay together. Chunks carry stable document IDs, content-fingerprint versions, source locations, and explicit headings where available; DOCX page numbers are not guessed.

From the repository root, `.\backend\venv\Scripts\python.exe .\backend\scripts\ingest_documents.py` creates an offline review export. Add `--publish` to persist document records and chunks in Firestore, or `--remove "KQ Data\Example.docx"` to remove one stored source without deleting its file. Publication is idempotent and replaces old chunks atomically per document; missing files are never automatically deleted. Failed or review-required attempts return a nonzero exit code. Publishing again can restore a removed source still present in the input folder.

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
| `FIRESTORE_PROJECT_ID` | Firebase project ID, required for protected authentication endpoints |
| `FIREBASE_CREDENTIALS_PATH` | Service-account JSON path; empty selects Application Default Credentials |
| `RAW_DATA_DIR`, `PROCESSED_DATA_DIR` | Input documents and exported chunk locations |
| `CHROMA_PERSIST_DIR` | Vector storage directory |
| `RAG_CHUNK_SIZE`, `RAG_CHUNK_OVERLAP` | Character target and maximum overlap; corpus-tested defaults are 1600 and 250, pending retrieval evaluation |
| `EMBEDDING_MODEL_NAME`, `DEFAULT_GENERATION_MODEL_ID` | Embedding and generation model selection |

Relative filesystem settings are resolved from the backend directory. Model selection is not required for backend startup or document extraction.

### Authentication configuration

Email/Password and Google sign-in use Firebase ID tokens sent as `Authorization: Bearer <ID_TOKEN>`. Google sign-in requires an authorized application domain. Users default to `passenger`; `admin` access requires a trusted custom claim.

Locally, store the service-account file at `credentials\firebase-admin.json` and set `FIREBASE_CREDENTIALS_PATH='../credentials/firebase-admin.json'`. Credentials and the backend environment file are Git-ignored, but may still sync through cloud storage. Deployments should use managed secrets or Application Default Credentials.

| Endpoint | Access |
|---|---|
| `GET /api/v1/auth/me` | Authenticated passenger or administrator |
| `GET /api/v1/auth/admin-check` | Administrator only |
| `POST /api/v1/auth/profile/sync` | Create or synchronize the signed-in user's profile |
| `GET /api/v1/auth/profile` | Read the signed-in user's saved profile |

After login, call profile sync with no body (or `{}`). Profiles are stored in the default Firestore database under `users`, keyed by Firebase UID (URL-encoded when needed). Name, email, and role come from the verified token; creation time is preserved, and `last_login` records Firebase's sign-in time rather than token refreshes. Missing display names remain empty; passwords are never stored in profiles.

Firestore uses the existing backend credentials. Production-mode rules should deny direct client access; the Admin SDK bypasses those rules, so these backend endpoints enforce ownership. Missing profiles return 404 and unavailable storage returns 503. Stored roles are informational snapshots; authorization uses verified Firebase claims.

The client-access policy is provided in [firestore.rules](firestore.rules); it must be published separately in Firebase.

**Assign an administrator** from the backend directory:

```powershell
.\venv\Scripts\python.exe .\scripts\set_user_role.py --uid "<FIREBASE_USER_UID>" --role admin --project-id "<FIREBASE_PROJECT_ID>"
```

Use `--role passenger` to remove admin access. Changes revoke existing sessions and require a new sign-in; retry any reported partial failure.

**Run authentication and profile tests** from the same directory (mocked Firebase/Firestore; no real users are modified):

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```
