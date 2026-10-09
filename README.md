# Kenya Airways Customer Support RAG Chatbot

**Project title:** A RAG-Based Kenyan Multilingual Virtual Assistant for Automated Airline Customer Support.

An academic web-based customer-support assistant designed to provide accessible, document-grounded information about Kenya Airways (KQ) services and policies in **English, Kiswahili, and mixed English-Kiswahili**.

The solution combines semantic document retrieval with generative AI to deliver relevant answers supported by source references. Its purpose is to improve access to airline information while supporting transparent response evaluation and knowledge-base administration.

The overview below presents the intended final system.

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

| Area                 | Information covered                                                         |
| -------------------- | --------------------------------------------------------------------------- |
| Booking              | Booking guidance and related policies                                       |
| Baggage              | Allowances, restrictions, and delayed, lost, or damaged baggage             |
| Check-in             | Procedures and requirements                                                 |
| Refunds and payments | Refund guidance and payment information                                     |
| Upgrades             | Upgrade information and applicable conditions                               |
| Travel requirements  | Travel documentation and policy guidance                                    |
| General support      | Frequently asked questions, contact information, and customer-care policies |

The application is an informational customer-support system rather than a transaction-processing or live reservation platform.

## Users

| User          | Capabilities                                                                                                              |
| ------------- | ------------------------------------------------------------------------------------------------------------------------- |
| Passenger     | Register and log in, ask support questions, view and end conversations, and provide feedback.                             |
| Administrator | Log in, manage and process documents, manage users, inspect conversations, view analytics, and manage/evaluate AI models. |

## Technology stack

| Layer                        | Technologies                                             | Purpose                                                              |
| ---------------------------- | -------------------------------------------------------- | -------------------------------------------------------------------- |
| Frontend                     | React                                                    | Passenger chat interface and administrator dashboard                 |
| Backend                      | Python, FastAPI, Uvicorn                                 | API endpoints and application services                               |
| Validation and configuration | Pydantic, python-dotenv                                  | Validated data models and environment-based settings                 |
| Authentication               | Firebase Authentication, Firebase Admin SDK              | User identity and role-based authorization                           |
| Application database         | Firestore                                                | Users, conversations, messages, feedback, and administrative records |
| Vector database              | ChromaDB                                                 | Document embeddings and semantic retrieval                           |
| RAG orchestration            | LangChain                                                | Retrieval, context preparation, and generation workflow              |
| Document processing          | python-docx                                              | Editable DOCX paragraphs and tables                                  |
| Embeddings                   | Multilingual E5-small (`intfloat/multilingual-e5-small`) | Local semantic representations of documents and questions            |
| Answer generation            | Generative AI APIs                                       | Evidence-based responses and comparative model evaluation            |

Backend package versions are listed in [requirements.txt](backend/requirements.txt).
