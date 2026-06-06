# Farm Vaidya Telugu Voice Agent — POC Project Tracker

**Goal:** End-to-end Telugu voice agent for telephonic conversations with RAG over Farm Vaidya's knowledge base.

**Deadline:** Within 8 working hours from credentials/resources being shared.

**Stack:** Pipecat · Soniox (STT) · Gemini 2.5 Flash on Vertex AI (Mumbai) · Cartesia (TTS) · Tata Tele (Telephony) · PostgreSQL · Docker

---

## Architecture

```
Caller → Tata Tele (SIP/WebSocket)
       → Pipecat Transport Layer
       → Soniox STT (Telugu)
       → STTGuardProcessor       (drops empty/noise frames)
       → RAGContextProcessor     (pgvector hybrid search → inject KB context)
       → LLMRetryProcessor       (catches LLM errors gracefully)
       → Gemini 2.5 Flash / Vertex AI (Mumbai)
       → Cartesia TTS (Telugu Voice)
       → Pipecat Transport Layer
       → Tata Tele → Caller

       ↓ (all stages write to)
       PostgreSQL (call_sessions · utterances · agent_responses · retrieval_logs · error_logs · performance_metrics · knowledge_chunks)
```

---

## Phase 1: Project Foundation ✅
- [x] **1.1** Python project structure (`src/`, `scripts/`, `src/rag/`, `src/services/`, `src/telephony/`, `src/db_logger/`)
- [x] **1.2** `requirements.txt` with all dependencies
- [x] **1.3** `.env.example` with all environment variables
- [x] **1.4** `docker-compose.yml` (app + postgres with pgvector)
- [x] **1.5** `Dockerfile`
- [x] **1.6** `schema.sql` — 7 tables + pgvector/FTS/trigram indexes
- [x] **1.7** `scripts/init_db.py` — applies schema to a live DB

## Phase 2: RAG System ✅
- [x] **2.1** `src/rag/chunker.py` — CSV row-per-chunk + sliding-window for text
- [x] **2.2** Chunking strategy: 300-token window / 50-token overlap; CSV rows as natural chunks
- [x] **2.3** `src/rag/embeddings.py` — Vertex AI `text-multilingual-embedding-002` (768-dim, Telugu)
- [x] **2.4** pgvector IVFFlat index + FTS + trigram indexes in schema.sql
- [x] **2.5** `scripts/ingest_kb.py` + `src/rag/ingestion.py` — bulk ingest + upsert
- [x] **2.6** `src/rag/retriever.py` — hybrid: cosine similarity + PostgreSQL FTS merge

## Phase 3: Core Pipecat Pipeline ✅
- [x] **3.1** `src/pipeline.py` — base Pipeline with all processors chained
- [x] **3.2** Soniox STT with Telugu language code (`te`)
- [x] **3.3** `src/services/llm.py` — Gemini 2.5 Flash via Vertex AI (routes google-generativeai → Vertex AI backend)
- [x] **3.4** Cartesia TTS with Telugu voice + sonic-multilingual model
- [x] **3.5** `RAGContextProcessor` — injects retrieved KB context into system prompt each turn
- [x] **3.6** Conversational memory — Pipecat maintains `LLMMessagesFrame` history per session

## Phase 4: Telephony Integration ✅
- [x] **4.1** `src/telephony/tata_tele.py` — WebSocket transport with Silero VAD
- [x] **4.2** `src/server.py` — `/ws/call` endpoint; session lifecycle start/end
- [x] **4.3** Concurrent sessions — one asyncio task per WebSocket connection
- [x] **4.4** Session isolation — UUID per call, per-session DB rows, separate pipeline instances

## Phase 5: Database & Logging ✅
- [x] **5.1** `src/database.py` — asyncpg pool (min 2, max 20 connections)
- [x] **5.2** `log_session_start` / `log_session_end`
- [x] **5.3** `log_utterance` / `log_response`
- [x] **5.4** `log_retrieval` — stores query, chunk list, top score, latency
- [x] **5.5** `log_error` — typed error rows (stt_failure, llm_timeout, etc.)
- [x] **5.6** `log_metrics` — per-turn STT/retrieval/LLM/TTS/E2E latencies

## Phase 6: Error Handling ✅
- [x] **6.1** `STTGuardProcessor` — drops empty transcriptions, speaks "please repeat" to caller
- [x] **6.2** `tenacity` retry on Vertex AI embeddings (3 attempts, exponential backoff)
- [x] **6.3** Empty retrieval → `NO_CONTEXT_RESPONSE` prompt fallback (Telugu)
- [x] **6.4** `LLMRetryProcessor` — catches `ErrorFrame` from LLM, logs, continues
- [x] **6.5** `WebSocketDisconnect` caught in server.py; session marked "dropped"

## Phase 7: Testing & Validation ⬜
- [ ] **7.1** Unit tests for RAG chunker and retriever (pytest)
- [x] **7.2** Integration test — live calls in progress (API keys available)
- [x] **7.3** Concurrent session load test — live environment (5 simultaneous WebSocket connections)
- [x] **7.4** Telugu conversation correctness spot-check — live testing active

## Phase 8: Deployment ✅
- [x] **8.1** `podman compose up` on Fedora server (replaced Docker with Podman — `compose.yml`)
- [x] **8.2** NGROK tunnel for public WebSocket exposure (`ngrok.yml`)
- [x] **8.3** Health check `/health` — verified against live server
- [x] **8.4** `scripts/ingest_kb.py` run against real KB

## Phase 9: Documentation ✅
- [x] **9.1** `README.md` — Podman + NGROK setup, env vars, startup commands
- [x] **9.2** Architecture diagram (Mermaid) — `docs/architecture_diagram.md`
- [x] **9.3** Technical notes — `docs/technical_notes.md`
- [x] **9.4** Deployment guide — `docs/deployment_guide.md` (Fedora · Podman · NGROK)

---

## Pending Inputs

| Item | Status | Notes |
|------|--------|-------|
| Knowledge base files | Received | Ingested — 2,307 Q&A chunks in pgvector |
| Soniox API key | Received | Live |
| Cartesia API key + Telugu voice ID | Received | Live |
| Vertex AI service account JSON | Received | Live |
| Tata Tele credentials + WebSocket endpoint | Received | Live calls in progress |

---

## Key Design Decisions

| Concern | Decision | Rationale |
|---------|----------|-----------|
| Vector store | pgvector (PostgreSQL extension) | Single DB, no extra infra |
| Embedding model | `text-multilingual-embedding-002` (Vertex AI) | Telugu support, same cloud |
| Chunking | CSV row-per-chunk; sliding window (300 tok / 50 overlap) for text | Crop data is tabular |
| Retrieval | Semantic cosine + BM25/FTS hybrid | Crop codes benefit from exact match |
| Session isolation | UUID session ID, per-session pipeline + DB rows | Prevents cross-call contamination |
| Concurrency | asyncio + one Pipecat pipeline per WebSocket | Pipecat is async-native |
| Telephony transport | WebSocket server (Tata Tele connects to us) | Most modern ITSP platforms support WebSocket streaming |
| LLM backend | Vertex AI via `google-generativeai` + `GOOGLE_GENAI_USE_VERTEXAI=true` | Reuses Pipecat's GoogleLLMService, no custom service needed |

---

## File Map

```
D:\FarmV-POC\
├── .env.example              ← copy to .env and fill credentials
├── .gitignore
├── docker-compose.yml        ← postgres (pgvector) + voice_agent
├── Dockerfile
├── requirements.txt
├── schema.sql                ← auto-applied by Docker postgres entrypoint
├── TASKS.md                  ← this file
├── scripts/
│   ├── init_db.py            ← apply schema to existing DB
│   └── ingest_kb.py          ← ingest knowledge_base/ → pgvector
└── src/
    ├── config.py             ← typed settings from .env
    ├── database.py           ← asyncpg connection pool
    ├── pipeline.py           ← main Pipecat pipeline (per-session)
    ├── prompts.py            ← system prompt + Telugu canned responses
    ├── server.py             ← FastAPI: /health /metrics /ws/call
    ├── error_handler.py      ← STTGuard + LLMRetry processors
    ├── rag/
    │   ├── chunker.py        ← CSV + sliding-window chunking
    │   ├── embeddings.py     ← Vertex AI embed_texts / embed_query
    │   ├── ingestion.py      ← bulk chunk + embed + upsert
    │   └── retriever.py      ← hybrid cosine + FTS retrieval
    ├── services/
    │   └── llm.py            ← Vertex AI LLM service factory
    ├── telephony/
    │   └── tata_tele.py      ← WebSocket transport (Silero VAD)
    └── db_logger/
        └── loggers.py        ← async DB logging for all events
```

---

## Progress Log

| Date | Update |
|------|--------|
| 2026-06-06 | Phases 1–6 implemented. Awaiting credentials for integration testing. |
| 2026-06-06 | Architecture diagram created (docs/architecture_diagram.md). |
| 2026-06-06 | API keys received. Live testing active via Tata Tele SmartFlo calls. |
| 2026-06-06 | Deployment switched to Podman + NGROK on Fedora server (compose.yml, ngrok.yml). |
| 2026-06-06 | Documentation complete: README.md, deployment_guide.md, technical_notes.md. Phases 8–9 done. |
| 2026-06-06 | Remaining: 7.1 unit tests; demo video. |
