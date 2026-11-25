# Kenya Airways Customer Support RAG Chatbot

An academic, multilingual customer-support application for Kenya Airways. The
system answers policy questions in English, Kiswahili, and mixed
English–Kiswahili, provides source-backed responses, handles supported live
flight enquiries, and gives administrators tools for knowledge-base,
conversation, human-support, model, and analytics management.

The chatbot provides information and guidance. It does not make bookings,
accept payments, change tickets, or replace official Kenya Airways channels.

## Main features

- **Passenger chatbot:** Passengers can ask Kenya Airways support questions
  through a responsive interface designed for mobile, laptop, and desktop use.
- **Secure sign-in:** Passengers can register and sign in using an email and
  password or their Google account through Firebase Authentication.
- **Multilingual conversations:** The chatbot supports English, Kiswahili, and
  questions that naturally combine both languages.
- **Conversation history:** Signed-in passengers can revisit previous chats,
  continue active conversations, end conversations, and submit feedback.
- **Source-backed answers:** Policy answers are generated from the Kenya
  Airways knowledge base and include references to the supporting sources.
- **Intelligent query routing:** The system distinguishes greetings, questions
  about its capabilities, unsupported languages, booking requests, policy
  questions, and live flight-information enquiries, then sends each request to
  the appropriate handler.
- **Live flight information:** Supported Kenya Airways flight schedule and
  status enquiries are retrieved through the OAG Flight Info API.
- **Safe booking guidance:** The chatbot explains booking procedures and
  directs passengers to official booking channels instead of attempting to
  make bookings or process payments.
- **Human support:** Passengers can request assistance from a person, while
  administrators can accept, manage, respond to, and resolve support requests.
- **Knowledge-base management:** Administrators can upload, process, edit,
  version, restore, and remove the documents used by the RAG system.
- **Administration dashboard:** Authorized administrators can manage users and
  AI models, inspect conversations, handle support requests, and review usage,
  feedback, response-time, and operational statistics.
- **Configurable AI models:** Administrators can use supported generation
  models supplied through Groq and OpenRouter without changing the retrieval
  pipeline.

## System architecture

```text
React/Vite frontend
        |
        | Firebase ID token
        v
FastAPI backend
   |-- intent and language routing
   |-- RAG retrieval and grounded generation
   |-- OAG flight-information routing
   |-- human-support workflow
   |
   |-- Firebase Authentication / Firestore
   |-- ChromaDB + multilingual E5-small
   |-- Groq / OpenRouter
   `-- OAG Flight Info API
```

| Layer | Technology | Purpose |
| --- | --- | --- |
| Frontend | React 19, Vite, Lucide React | Passenger and administrator interfaces |
| API | FastAPI, Uvicorn, Pydantic | Validated application endpoints and services |
| Authentication | Firebase Authentication, Firebase Admin SDK | Email/password, Google sign-in, and roles |
| Application data | Cloud Firestore | Profiles, conversations, support, documents, feedback, and analytics |
| Vector index | ChromaDB | Persistent local semantic index |
| Embeddings | `intfloat/multilingual-e5-small` | Multilingual dense retrieval |
| Lexical retrieval | BM25 | Exact-term and policy-name matching |
| Generation | Groq and OpenRouter | Grounded answer generation |
| Flight information | OAG Flight Info API v2 | Supported KQ schedule/status lookups |

## Production retrieval configuration

The application implements the non-reranked **E20** comparative configuration:

| Setting | Value |
| --- | --- |
| Knowledge base | Bilingual mapped search representations |
| Authoritative content | English parent chunks |
| Kiswahili mapping | Kiswahili representations map to the same English parent IDs |
| Parent chunk target | 1,800 characters |
| Maximum overlap | 360 characters (20%) |
| Dense model | Multilingual E5-small |
| Lexical retrieval | BM25 |
| Fusion | Equal-weight Reciprocal Rank Fusion |
| RRF constant | `k=10` |
| Dense candidates | 40 |
| Lexical candidates | 40 |
| Final result | Top 5 unique parent chunks |
| Production reranker | Disabled |

The mapped representation design improves Kiswahili retrieval without creating
two competing authoritative corpora. Context construction remains bounded to
8,000 rendered characters, two source documents, and complete chunks.

### Reranker experiments

Comparative experiments evaluate the local cross-encoder
`BAAI/bge-reranker-v2-m3` at different candidate depths. The reranker is
pretrained, runs locally, and was not fine-tuned. It is intentionally **not
enabled in the deployed E20 pipeline**. See:

- [Reranker evaluation results](backend/data/evaluation/reranker-evaluation/reranker_evaluation_results.json)
- [Reranker statistical analysis](backend/data/evaluation/reranker-evaluation/reranker_statistical_analysis.json)

## Prerequisites

- Python 3.12
- Node.js 20.19 or newer and npm
- A Firebase project with Authentication and Firestore enabled
- A Firebase service account for local backend development
- At least one configured Groq or OpenRouter generation model
- An OAG API subscription only when live flight information is required
- Sufficient disk and memory for PyTorch, multilingual E5-small, and ChromaDB

## Local setup

### 1. Clone the repository

```bash
git clone <repository-url>
cd <repository-directory>
```

### 2. Configure Firebase

In the Firebase console:

1. Create or select a Firebase project.
2. Enable Firestore.
3. Under **Authentication > Sign-in method**, enable Email/Password and Google.
4. Under **Authentication > Settings > Authorized domains**, add the local and
   deployed frontend domains.
5. Register a Web app and retain its public web configuration values.
6. Create a service-account key for local development and keep it outside Git.
7. Deploy the repository rules and indexes when using the Firebase CLI:

```bash
firebase deploy --only firestore:rules,firestore:indexes
```

### 3. Set up the backend

Windows PowerShell:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

Linux/macOS:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

Edit `backend/.env`. At minimum, configure:

```dotenv
ENVIRONMENT="development"
CORS_ORIGINS="http://localhost:5174"
FIRESTORE_PROJECT_ID="your-firebase-project-id"
FIREBASE_CREDENTIALS_PATH="../credentials/firebase-admin.json"

OPENROUTER_API_KEY="your-key-if-used"
GROQ_API_KEY="your-key-if-used"
DEFAULT_GENERATION_MODEL_ID="groq_gpt_oss_120b"

OAG_API_KEY="your-key-if-used"
```

The checked-in `.env.example` contains every retrieval, context, model, upload,
rate-limit, and OAG setting. Do not commit `.env` or service-account JSON.

Start the API:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Verify `http://127.0.0.1:8000/health`. API documentation is available at
`http://127.0.0.1:8000/docs`.

### 4. Set up the frontend

Open another terminal from the repository root.

Windows PowerShell:

```powershell
cd frontend
npm install
Copy-Item .env.example .env
npm run dev
```

Linux/macOS:

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

Populate `frontend/.env`:

```dotenv
VITE_FIREBASE_API_KEY="..."
VITE_FIREBASE_AUTH_DOMAIN="your-project.firebaseapp.com"
VITE_FIREBASE_PROJECT_ID="your-project-id"
VITE_FIREBASE_APP_ID="..."
VITE_API_BASE_URL=""
```

During local development, leave `VITE_API_BASE_URL` blank. Vite proxies `/api`
to `http://127.0.0.1:8000`. Open `http://localhost:5174`.

### 5. Create an administrator

Public registration creates passenger accounts. Assign administrator access
only from a trusted backend environment:

```bash
cd backend
python scripts/set_user_role.py --uid <firebase-uid> --role admin --project-id <firebase-project-id>
```

The user must sign out and sign in again after the role change.

## Knowledge-base setup

Place supported `.docx` source documents in `backend/data/raw`. Previewing is
read-only:

```bash
cd backend
python scripts/ingest_documents.py
```

After reviewing the report, publish the documents to Firestore:

```bash
python scripts/ingest_documents.py --publish
```

Synchronize the persistent bilingual ChromaDB index:

```bash
python scripts/index_documents.py
```

The sync command reuses unchanged embeddings. Use `--rebuild` only after a
deliberate embedding model, chunking, or representation change:

```bash
python scripts/index_documents.py --rebuild
```

Test retrieval without regenerating the active index:

```bash
python scripts/index_documents.py --query "What is the baggage allowance?" --language en
```

Documents can also be uploaded and processed from the administrator dashboard.

## Tests and production build

```bash
cd backend
pip install -r requirements-test.txt
pytest
```

```bash
cd frontend
npm run build
```

The frontend production output is written to `frontend/dist`.

## Deployment

The application supports a split deployment: the React frontend on Render and
the resource-intensive FastAPI/RAG backend on Azure. The frontend and backend
may also be deployed together elsewhere when persistent storage and sufficient
memory are available.

### Render: frontend static site

1. Push the repository to a Git provider and create a **Static Site** in Render.
2. Set the root directory to `frontend`.
3. Set the build command to `npm install && npm run build`.
4. Set the publish directory to `dist`.
5. Add the four `VITE_FIREBASE_*` values.
6. Set `VITE_API_BASE_URL` to the public Azure backend origin, without
   `/api/v1`.
7. Add a rewrite from `/*` to `/index.html` for React client-side routes.
8. Add the Render hostname to Firebase Authorized domains.
9. Add the Render origin to backend `CORS_ORIGINS`.

Vite variables are embedded at build time, so redeploy after changing them.
If the backend hostname changes, also update the Content Security Policy
`connect-src` entry in `frontend/index.html` before rebuilding.

### Azure: FastAPI and RAG backend

An Azure Linux VM is suitable because ChromaDB and the embedding cache require
persistent storage and the E5 model needs more memory than a minimal web
instance. Provision a VM sized for the corpus and expected concurrency, then:

1. Install Python 3.12, Git, and a reverse proxy such as Nginx.
2. Clone the repository and create `backend/.venv` as in local setup.
3. Install `backend/requirements.txt`.
4. Store production settings as protected environment variables or in an
   uncommitted `backend/.env` readable only by the service account.
5. Use Azure Managed Identity/Application Default Credentials where possible;
   otherwise securely provision the Firebase service-account file.
6. Persist and back up `chroma_db`, `model_cache`, and required source files.
7. Copy or synchronize the validated ChromaDB index. Rebuild it only when the
   corpus or retrieval fingerprint changes.
8. Run the API behind a process supervisor and Nginx, for example:

```bash
cd /opt/kq-chatbot/backend
/opt/kq-chatbot/backend/.venv/bin/uvicorn app.main:app \
  --host 127.0.0.1 --port 8000 --workers 1
```

Use one worker unless deployment testing confirms that multiple processes can
safely share local model memory and the ChromaDB lifecycle. Expose only
HTTP/HTTPS through the Azure network security group, terminate TLS at Nginx,
and proxy requests to port 8000.

Recommended production settings include:

```dotenv
ENVIRONMENT="production"
DEBUG="false"
CORS_ORIGINS="https://your-render-site.onrender.com"
RAG_WARMUP_ON_STARTUP="true"
```

Add provider credentials and Firebase/OAG settings through Azure secrets or
protected environment configuration. Configure the health probe for `/health`.
After deployment, verify health, authenticated API calls, CORS, Google sign-in,
one RAG query, and one authorized administrator action.

### Deployment security checklist

- Never expose Firebase Admin, Groq, OpenRouter, or OAG credentials to Vite.
- Keep production Firebase credentials and `.env` files out of source control.
- Allow only known frontend origins through CORS.
- Add every deployed frontend domain to Firebase Authorized domains.
- Keep the Google sign-in CSP sources and `same-origin-allow-popups` behaviour.
- Serve both frontend and backend over HTTPS.
- Restrict administrator assignment to trusted backend operations.
- Back up Firestore and the active ChromaDB generation before major changes.

## Repository layout

```text
backend/
  app/                 FastAPI routes, services, RAG, security, and models
  data/raw/            Source DOCX documents
  experiments/         Comparative retrieval and reranker work
  scripts/             Ingestion, indexing, roles, evaluation, and maintenance
  tests/               Backend regression tests
frontend/
  src/admin/           Administrator dashboard
  src/passenger/       Passenger landing, authentication, and chat UI
  src/auth/            Frontend authentication flow
chroma_db/             Persistent active vector-index generations
docs/                  Evaluation reports and project documentation
```

## Notes

- The system uses pretrained embedding, reranking, and generation models; it
  does not train a new language model.
- Unsupported languages and general conversational questions bypass retrieval.
- Only genuine supported flight-information intents reach OAG.
- Policy questions use RAG and retain confidence, citation, clarification, and
  fallback safeguards.
