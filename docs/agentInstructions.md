# KQ Customer Support Chatbot

## Project Context

**Project Title:** A RAG-Based Kenyan Multilingual Virtual Assistant for Automated Airline Customer Support

This is a final-year university project to develop a web-based customer support chatbot specifically for **Kenya Airways (KQ)**.

The chatbot uses Retrieval-Augmented Generation (RAG) to retrieve relevant information from a Kenya Airways knowledge base before generating an answer. Responses should be grounded in the retrieved knowledge-base content and should not invent information.

---

# 1. Project Scope

## Supported Languages

* English
* Kiswahili
* Mixed English–Kiswahili queries

**Sheng is outside the project scope.**

## Supported Customer-Support Areas

* Booking information
* Baggage
* Check-in
* Refunds
* Payment information
* Upgrades
* Travel requirements
* General Kenya Airways FAQs and policies

## Out of Scope

Do NOT implement:

* Ticket booking
* Payment processing
* Flight modification
* Live reservation systems
* Live airline operational systems
* Voice interaction
* Mobile applications
* Internal airline staff workflows

If the knowledge base does not contain enough information to answer a question, the chatbot should use a fallback response instead of inventing an answer. Where appropriate, it can offer clarification or a human-support option.

---

# 2. Technology Stack

## Frontend

* React.js
* Passenger chatbot interface
* Admin dashboard

## Backend

* Python
* FastAPI
* LangChain

## Data

* Firebase / Firestore — application data
* ChromaDB — vector storage and semantic retrieval

## AI

The system supports multiple AI models through APIs.

**Do not hard-code specific AI model names.** Model configuration should come from environment variables or application configuration.

The embedding model is separate from the generative AI models.

---

# 3. RAG Workflow

```text
User Question
      ↓
Query Processing
      ↓
Language Identification
      ↓
Query Embedding
      ↓
ChromaDB Similarity Search
      ↓
Retrieve Relevant Document Chunks
      ↓
Rank / Filter Results
      ↓
Context Builder
      ↓
Question + Retrieved Context
      ↓
Selected AI Model
      ↓
Generated Answer
      ↓
Response Handler
      ↓
Frontend
      ↓
User
```

If retrieval does not provide sufficient relevant information:

```text
Insufficient Retrieval
        ↓
Fallback / Clarification
        ↓
Human Support Option where appropriate
```

The system should preserve the sources used to generate each response.

---

# 4. Application / Logic Layer

## Query Processing

Receives the user's question, identifies whether it is in English, Kiswahili, or mixed language, and prepares the query for retrieval.

## Retrieval Module

Converts the question into a searchable representation and retrieves relevant knowledge-base chunks from ChromaDB.

## Context Builder

Ranks retrieved chunks according to relevance and combines the most useful information into context for the AI model.

## Generation Module

Sends the original question together with the retrieved context to the selected AI model and receives the generated answer.

## Response Handler

Formats the generated answer and attaches relevant sources or citations before returning it to the frontend.

## Evaluation & Logging Service

Records:

* Conversations
* Messages
* Retrieved document chunks
* Similarity scores
* AI model used
* User feedback
* Errors and system events
* Model evaluation results
* Response-time information

---

# 5. Knowledge Base

The knowledge base consists mainly of Kenya Airways customer-support and policy documents.

Raw documents are stored in:

```text
backend/data/raw/
```

The source files are primarily `.docx` documents.

## Ingestion Pipeline

The pipeline should:

1. Load DOCX documents
2. Extract text
3. Clean and normalise the text
4. Split the text into meaningful chunks
5. Generate embeddings
6. Store chunks and embeddings in ChromaDB
7. Preserve metadata such as document, section, category, and chunk index

Do not manually duplicate document content inside source code.

---

# 6. Database Schema

The application database should follow this conceptual schema.

## USER

Stores passenger and administrator accounts.

| Field           | Type     | Description           |
| --------------- | -------- | --------------------- |
| `user_id`       | String   | Primary key           |
| `name`          | String   | User's name           |
| `email`         | String   | User email            |
| `password_hash` | String   | Hashed password       |
| `role`          | String   | `customer` or `admin` |
| `created_at`    | DateTime | Account creation time |
| `last_login`    | DateTime | Most recent login     |

---

## CONVERSATION

Stores individual customer conversations.

| Field             | Type     | Description                 |
| ----------------- | -------- | --------------------------- |
| `conversation_id` | String   | Primary key                 |
| `user_id`         | String   | Foreign key to USER         |
| `title`           | String   | Conversation title          |
| `language`        | String   | Main conversation language  |
| `started_at`      | DateTime | Conversation start time     |
| `ended_at`        | DateTime | Conversation end time       |
| `status`          | String   | Current conversation status |

Relationship:

```text
USER 1 ─────── * CONVERSATION
```

---

## MESSAGE

Stores individual messages exchanged during conversations.

| Field             | Type     | Description                                             |
| ----------------- | -------- | ------------------------------------------------------- |
| `message_id`      | String   | Primary key                                             |
| `conversation_id` | String   | Foreign key to CONVERSATION                             |
| `model_id`        | String   | Foreign key to AI_MODEL; nullable for customer messages |
| `sender_type`     | String   | Customer or chatbot                                     |
| `message_text`    | Text     | Message content                                         |
| `language`        | String   | Message language                                        |
| `timestamp`       | DateTime | Time message was created                                |
| `response_source` | String   | Indicates how the response was sourced                  |

Relationship:

```text
CONVERSATION 1 ─────── * MESSAGE
AI_MODEL 1 ─────── * MESSAGE
```

---

## FEEDBACK

Stores passenger feedback about chatbot responses.

| Field         | Type     | Description               |
| ------------- | -------- | ------------------------- |
| `feedback_id` | String   | Primary key               |
| `user_id`     | String   | Foreign key to USER       |
| `message_id`  | String   | Foreign key to MESSAGE    |
| `rating`      | Integer  | User rating               |
| `comment`     | Text     | Optional feedback comment |
| `created_at`  | DateTime | Feedback submission time  |

Relationships:

```text
USER 1 ─────── * FEEDBACK
MESSAGE 1 ─────── 0..1 FEEDBACK
```

---

## DOCUMENT

Stores information about knowledge-base documents.

| Field         | Type     | Description                      |
| ------------- | -------- | -------------------------------- |
| `document_id` | String   | Primary key                      |
| `filename`    | String   | Original file name               |
| `title`       | String   | Document title                   |
| `category`    | String   | Document category                |
| `source_url`  | String   | Original source where applicable |
| `admin_id`    | String   | Admin who uploaded/managed it    |
| `upload_date` | DateTime | Upload date                      |
| `status`      | String   | Document status                  |
| `version`     | String   | Document version                 |

Relationship:

```text
DOCUMENT 1 ─────── * DOCUMENT_CHUNK
```

Only administrators should be able to upload, update, or delete knowledge-base documents.

---

## DOCUMENT_CHUNK

Stores the smaller sections created from knowledge-base documents.

| Field         | Type     | Description                       |
| ------------- | -------- | --------------------------------- |
| `chunk_id`    | String   | Primary key                       |
| `document_id` | String   | Foreign key to DOCUMENT           |
| `chunk_text`  | Text     | Text contained in the chunk       |
| `section`     | String   | Source document section           |
| `category`    | String   | Chunk category                    |
| `chunk_index` | Integer  | Position of chunk within document |
| `created_at`  | DateTime | Chunk creation time               |

Relationship:

```text
DOCUMENT 1 ─────── * DOCUMENT_CHUNK
```

---

## EMBEDDING

Stores information about the vector representation of a document chunk.

| Field              | Type     | Description                    |
| ------------------ | -------- | ------------------------------ |
| `embedding_id`     | String   | Primary key                    |
| `chunk_id`         | String   | Foreign key to DOCUMENT_CHUNK  |
| `embedding_model`  | String   | Embedding model used           |
| `vector_reference` | String   | Reference to the stored vector |
| `created_at`       | DateTime | Embedding creation time        |

Relationship:

```text
DOCUMENT_CHUNK 1 ─────── 1 EMBEDDING
```

The actual vectors are stored/retrieved through ChromaDB. Do not treat the embedding as the generative AI model.

---

## MESSAGE_CHUNK

Records which knowledge-base chunks were retrieved for a particular message.

| Field              | Type    | Description                                |
| ------------------ | ------- | ------------------------------------------ |
| `message_chunk_id` | String  | Primary key                                |
| `message_id`       | String  | Foreign key to MESSAGE                     |
| `chunk_id`         | String  | Foreign key to DOCUMENT_CHUNK              |
| `similarity_score` | Float   | Retrieval similarity score                 |
| `retrieval_rank`   | Integer | Position of the chunk in retrieval results |

This provides traceability between a chatbot response and the information retrieved from the knowledge base.

Relationships:

```text
MESSAGE * ─────── * DOCUMENT_CHUNK
```

---

## AI_MODEL

Stores information about the AI models available to the system.

| Field              | Type     | Description            |
| ------------------ | -------- | ---------------------- |
| `model_id`         | String   | Primary key            |
| `model_name`       | String   | Model name             |
| `provider`         | String   | Model provider         |
| `model_version`    | String   | Model version          |
| `language_support` | String   | Supported languages    |
| `status`           | String   | Active/inactive status |
| `created_at`       | DateTime | Record creation time   |

The system should support multiple models so they can be evaluated and compared.

Do not hard-code specific model names.

---

## ADMIN_ACTIVITY

Stores an audit trail of administrator actions.

| Field         | Type     | Description             |
| ------------- | -------- | ----------------------- |
| `activity_id` | String   | Primary key             |
| `admin_id`    | String   | Foreign key to USER     |
| `action`      | String   | Action performed        |
| `target_type` | String   | Type of affected object |
| `target_id`   | String   | ID of affected object   |
| `timestamp`   | DateTime | Time of action          |

Example actions:

```text
Upload Document
Update Document
Delete Document
View Conversations
View Analytics
Manage AI Model
Evaluate AI Model
```

---

# 7. User Roles

## Passenger

Can:

* Register
* Log in
* Ask support questions
* View conversation history
* End conversations
* Submit feedback

## Administrator

Can:

* Log in
* Upload documents
* Update documents
* Delete documents
* Process the knowledge base
* View conversations
* View analytics
* Manage users
* Manage AI models
* Evaluate AI models

---

# 8. Frontend Pages

## Passenger

* Login
* Registration
* Chatbot
* Conversation History

## Admin

* Dashboard Overview
* Analytics
* Document Management
* Conversations
* AI Model Management / Evaluation
* User Management

## Chatbot Interface

Include:

* New Conversation
* Previous Conversations
* Language Selection
* Chat Messages
* Message Input
* Send Button
* Sources / Citations where applicable

---

# 9. Admin Dashboard

## Dashboard Overview

Provide a summary of:

* Users
* Conversations
* Documents
* System activity

## Analytics

Show:

* Conversation activity
* User interactions
* Feedback
* Usage information
* AI model evaluation results
* Performance information

## Document Management

Administrators should be able to:

* Upload
* View
* Update
* Delete
* Process documents

Display:

* Document name
* Title
* Category
* Version
* Upload date
* Uploaded by
* Status
* Actions

## Conversations

Allow administrators to inspect previous customer conversations and messages.

## AI Models

Allow administrators to view configured models and compare their evaluation results.

Evaluation should support:

* Faithfulness
* Answer Relevance
* Context Precision
* Context Recall

---

# 10. Backend Structure

Use a modular structure similar to:

```text
backend/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── api/
│   ├── models/
│   ├── services/
│   ├── rag/
│   └── utils/
├── data/
│   ├── raw/
│   └── processed/
├── requirements.txt
└── .env
```

Keep API keys, credentials, and configuration secrets in environment variables.

---

# 11. Required Technical Skills

## Frontend

* React.js
* Component-based architecture
* State management
* Form handling
* API integration
* Responsive UI
* Authentication-aware interfaces
* Admin dashboard development

## Backend

* Python
* FastAPI
* REST API design
* Request validation
* Error handling
* Authentication
* Role-based access control
* Modular service architecture

## RAG / AI

* Retrieval-Augmented Generation
* Document processing
* Text preprocessing
* Document chunking
* Embeddings
* Semantic search
* Vector databases
* Context construction
* Prompt construction
* AI model API integration
* Multilingual queries
* English–Kiswahili code-switching
* RAG evaluation

## Databases

* Firebase
* Firestore
* ChromaDB
* Data modelling
* Document metadata
* Retrieval traceability

## Software Engineering

* Clean code
* Modular architecture
* Separation of concerns
* Reusable components
* API/service separation
* Environment configuration
* Input validation
* Error handling
* Logging
* Testing
* Git/version control

## Security

* Secure authentication
* Password hashing
* Role-based authorisation
* Environment variables for secrets
* Input validation
* Protected admin endpoints
* Safe API responses

---

# 12. Development Rules

1. Do not invent functionality outside the project scope.
2. Do not hard-code AI model names.
3. Do not expose API keys or credentials in source code.
4. Keep frontend, backend, RAG, and database responsibilities separate.
5. Use modular and maintainable code.
6. Use clear and consistent naming.
7. Reuse existing working code where possible.
8. Handle errors and failed retrieval gracefully.
9. Validate user input.
10. Protect administrator functionality with role-based access control.
11. Preserve conversation and retrieval traceability.
12. Do not generate unsupported factual answers when the required information is unavailable from the knowledge base.
13. Keep configuration separate from application logic.
14. Avoid unnecessary dependencies.
15. Do not rewrite large parts of the project without a clear reason.
16. Keep implementation consistent with the documented architecture and database schema.
17. Check dependencies before changing existing functionality.
18. Prefer simple solutions over unnecessary abstraction.
19. Keep sensitive configuration in `.env`.
20. Do not place secrets in Git.

---

# 13. Development Approach

Do not generate the entire application at once.

Develop incrementally:

1. Project structure and configuration
2. Authentication and user roles
3. Firebase/Firestore integration
4. Knowledge-base ingestion
5. ChromaDB setup and retrieval
6. RAG pipeline
7. AI model integration
8. Chatbot API
9. Passenger frontend
10. Admin dashboard
11. Logging and feedback
12. Analytics
13. AI model evaluation
14. Testing and refinement

Before implementing each stage:

* Inspect the existing code.
* Identify what is already working.
* Reuse existing components where appropriate.
* Make the smallest necessary changes.
* Keep the implementation consistent with the architecture and schema.

---

# 14. TODO List (Skipped for now)

Track approved follow-up work here so future increments remain aligned with the project plan.

* [ ] Improve PDF ingestion by adding `pdfplumber` for PDF table extraction, then format extracted PDF table rows into readable text before chunking.

---

# 15. Coding Style

Write code that is:

* Clear
* Simple
* Readable
* Modular
* Maintainable
* Properly documented where necessary

Avoid:

* Over-engineering
* Unnecessary abstractions
* Duplicate code
* Hard-coded secrets
* Hard-coded model names
* Unnecessary dependencies
* Large files containing unrelated responsibilities

Prioritise working functionality first, then improve the structure where necessary.

---

# 16. Important Project Constraint

This is a **final-year academic project**, so all implementation decisions should remain consistent with the documented:

* System architecture
* Use cases
* Sequence flow
* Class design
* Database schema
* Project scope

When there is a choice between adding a new feature and keeping the implementation aligned with the documented design, **prefer alignment with the documented design unless the change is necessary and explicitly approved.**
