# Architecture Diagram — Farm Vaidya Telugu Voice Agent

## 1. System Overview

```mermaid
flowchart TD
    %% ─────────────────────────────────────────
    %% CALLER
    %% ─────────────────────────────────────────
    CALLER(["Caller (Farmer)\nPSTN / Mobile"])

    %% ─────────────────────────────────────────
    %% TELEPHONY
    %% ─────────────────────────────────────────
    subgraph TELCO["Telephony — Tata Tele Business Services"]
        SIP["SIP / WebSocket Gateway"]
    end

    %% ─────────────────────────────────────────
    %% VOICE AGENT APPLICATION
    %% ─────────────────────────────────────────
    subgraph APP["Farm Vaidya Voice Agent  ·  FastAPI + Pipecat  ·  asyncio"]
        direction TB
        TRANSPORT["WebSocket Transport\n/ws/call  ·  one session per call"]
        VAD["Silero VAD\nVoice Activity Detection"]
        STT["Soniox STT\nTelugu  ·  language = te"]
        STTG["STTGuardProcessor\nDrops empty / noise frames\nSpeaks 'please repeat' canned response"]
        RAGPROC["RAGContextProcessor\nHybrid retrieval → inject KB context\ninto system prompt each turn"]
        LLMRETRY["LLMRetryProcessor\nCatches ErrorFrame · logs · continues"]
        LLM["Gemini 2.5 Flash\nVertex AI  ·  asia-south1 (Mumbai)\nInstructs LLM to respond only in Telugu"]
        TTS["Cartesia TTS\nTelugu voice  ·  sonic-multilingual"]
    end

    %% ─────────────────────────────────────────
    %% RAG STACK
    %% ─────────────────────────────────────────
    subgraph RAG["RAG Stack"]
        direction TB
        EMBED["Vertex AI Embeddings\ntext-multilingual-embedding-002\n768-dim  ·  Telugu-native"]
        VEC["pgvector IVFFlat Index\nCosine Similarity  ·  lists = 50"]
        FTS["PostgreSQL FTS\ntsvector  ·  simple tokeniser"]
        MERGE["Hybrid Merge\nSemantic results (priority) +\nFTS scores × 0.5  →  top-k chunks"]
    end

    %% ─────────────────────────────────────────
    %% DATABASE
    %% ─────────────────────────────────────────
    subgraph DB["PostgreSQL + pgvector  ·  asyncpg pool (min 2 / max 20)"]
        direction LR
        KC[(knowledge_chunks\nvectors + FTS index)]
        CS[(call_sessions)]
        UTT[(utterances)]
        AR[(agent_responses)]
        RL[(retrieval_logs)]
        EL[(error_logs)]
        PM[(performance_metrics)]
    end

    %% ─────────────────────────────────────────
    %% OBSERVABILITY
    %% ─────────────────────────────────────────
    subgraph OBS["Logging & Observability"]
        DBLOG["db_logger\nasync loggers"]
        HEALTH["/health"]
        METRICS["/metrics"]
    end

    %% ─────────────────────────────────────────
    %% MAIN CALL FLOW
    %% ─────────────────────────────────────────
    CALLER -->|"PSTN / RTP audio"| SIP
    SIP     -->|"WebSocket audio stream"| TRANSPORT
    TRANSPORT --> VAD
    VAD     -->|"Speech frames"| STT
    STT     -->|"TranscriptionFrame"| STTG
    STTG    -->|"Valid utterance"| RAGPROC
    RAGPROC -->|"Augmented system prompt"| LLMRETRY
    LLMRETRY --> LLM
    LLM     -->|"Telugu text response"| TTS
    TTS     -->|"Audio frames"| TRANSPORT
    TRANSPORT -->|"Audio stream"| SIP
    SIP     -->|"Synthesised voice"| CALLER

    %% ─────────────────────────────────────────
    %% RAG RETRIEVAL PATH
    %% ─────────────────────────────────────────
    RAGPROC -->|"embed query"| EMBED
    EMBED   -->|"768-dim vector"| VEC
    RAGPROC -->|"text query"| FTS
    VEC     --> MERGE
    FTS     --> MERGE
    MERGE   -->|"top-k KB chunks"| RAGPROC
    KC      -. "stored vectors" .-> VEC
    KC      -. "FTS index" .-> FTS

    %% ─────────────────────────────────────────
    %% LOGGING WRITES (all async, per turn)
    %% ─────────────────────────────────────────
    TRANSPORT -->|"session_start / session_end"| DBLOG
    STTG      -->|"log_utterance"| DBLOG
    LLM       -->|"log_response"| DBLOG
    RAGPROC   -->|"log_retrieval"| DBLOG
    LLMRETRY  -->|"log_error"| DBLOG
    STT       -->|"STT latency"| DBLOG
    RAGPROC   -->|"retrieval latency"| DBLOG
    LLM       -->|"LLM latency"| DBLOG
    TTS       -->|"TTS latency"| DBLOG

    DBLOG --> CS
    DBLOG --> UTT
    DBLOG --> AR
    DBLOG --> RL
    DBLOG --> EL
    DBLOG --> PM

    HEALTH  -. "ping" .-> DB
    METRICS -. "query" .-> DB
```

---

## 2. Call Sequence (Single Turn)

```mermaid
sequenceDiagram
    autonumber
    actor Farmer as Caller (Farmer)
    participant TT  as Tata Tele<br/>WebSocket Gateway
    participant WS  as Pipecat<br/>Transport
    participant VAD as Silero VAD
    participant STT as Soniox STT<br/>(Telugu)
    participant RAG as RAGContext<br/>Processor
    participant VX  as Vertex AI<br/>(Embeddings + LLM)
    participant LLM as Gemini 2.5 Flash
    participant TTS as Cartesia TTS<br/>(Telugu)
    participant DB  as PostgreSQL

    Farmer ->> TT: Speaks in Telugu (audio)
    TT     ->> WS: WebSocket audio frames
    WS     ->> VAD: Raw audio
    VAD    ->> STT: Speech segment detected
    STT    ->> RAG: TranscriptionFrame (Telugu text)
    RAG    ->> VX:  Embed query (text-multilingual-embedding-002)
    VX     -->> RAG: 768-dim query vector
    RAG    ->> DB:  pgvector cosine search + FTS
    DB     -->> RAG: Top-k KB chunks
    RAG    ->> DB:  log_retrieval (query, chunks, score, latency)
    RAG    ->> LLM: Augmented system prompt + conversation history
    LLM    ->> VX:  Gemini 2.5 Flash inference (Vertex AI)
    VX     -->> LLM: Telugu text response
    LLM    ->> DB:  log_utterance + log_response + log_metrics
    LLM    ->> TTS: Telugu text
    TTS    -->> WS: Audio frames (synthesised voice)
    WS     ->> TT:  Audio stream
    TT     ->> Farmer: Synthesised Telugu voice
```

---

## 3. Component Summary

| Layer | Component | Technology |
|---|---|---|
| Telephony | Incoming / outgoing voice | Tata Tele Business Services (SIP → WebSocket) |
| Transport | WebSocket session management | Pipecat `WebsocketServerTransport` + FastAPI |
| VAD | End-of-speech detection | Silero VAD (built into Pipecat) |
| STT | Speech-to-text | Soniox — Telugu (`language=te`) |
| RAG | Knowledge retrieval | pgvector (cosine) + PostgreSQL FTS — hybrid merge |
| Embeddings | Query + chunk vectorisation | Vertex AI `text-multilingual-embedding-002` (768-dim) |
| LLM | Response generation | Gemini 2.5 Flash — Vertex AI, `asia-south1` (Mumbai) |
| TTS | Text-to-speech | Cartesia — `sonic-multilingual`, Telugu voice |
| Database | Persistence + vector store | PostgreSQL 15 + pgvector extension |
| Logging | Async event capture | `db_logger` → 6 PostgreSQL tables |
| Deployment | Containerisation | Docker Compose (app + postgres) |

---

## 4. Database Schema

```mermaid
erDiagram
    call_sessions {
        uuid    session_id    PK
        text    phone_number
        text    status
        int     duration_sec
        int     turn_count
        timestamptz started_at
        timestamptz ended_at
    }
    utterances {
        uuid    utterance_id  PK
        uuid    session_id    FK
        int     turn_number
        text    raw_text
        float   confidence
        float   stt_latency_ms
        timestamptz created_at
    }
    agent_responses {
        uuid    response_id   PK
        uuid    utterance_id  FK
        uuid    session_id    FK
        text    response_text
        float   llm_latency_ms
        timestamptz created_at
    }
    retrieval_logs {
        uuid    retrieval_id  PK
        uuid    utterance_id  FK
        uuid    session_id    FK
        text    query
        jsonb   chunk_ids
        float   top_score
        float   retrieval_latency_ms
        timestamptz created_at
    }
    error_logs {
        uuid    error_id      PK
        uuid    session_id    FK
        text    error_type
        text    message
        text    stack_trace
        timestamptz created_at
    }
    performance_metrics {
        uuid    metric_id     PK
        uuid    session_id    FK
        int     turn_number
        float   stt_ms
        float   retrieval_ms
        float   llm_ms
        float   tts_ms
        float   e2e_ms
        timestamptz created_at
    }
    knowledge_chunks {
        uuid    chunk_id      PK
        text    source_file
        text    question_number
        text    question
        text    content
        vector  embedding
        tsvector content_tsv
        timestamptz ingested_at
    }

    call_sessions    ||--o{ utterances         : "has"
    call_sessions    ||--o{ agent_responses     : "has"
    call_sessions    ||--o{ retrieval_logs      : "has"
    call_sessions    ||--o{ error_logs          : "has"
    call_sessions    ||--o{ performance_metrics : "has"
    utterances       ||--o| agent_responses     : "answered by"
    utterances       ||--o| retrieval_logs      : "retrieval for"
```

---

## 5. Concurrency Model

```
                     ┌─────────────────────────────────────────────┐
                     │         FastAPI  (single process)           │
                     │                                             │
  Call 1 ─WebSocket─►│  asyncio Task 1  ──► Pipeline Instance 1  │
  Call 2 ─WebSocket─►│  asyncio Task 2  ──► Pipeline Instance 2  │
  Call 3 ─WebSocket─►│  asyncio Task 3  ──► Pipeline Instance 3  │
  Call 4 ─WebSocket─►│  asyncio Task 4  ──► Pipeline Instance 4  │
  Call 5 ─WebSocket─►│  asyncio Task 5  ──► Pipeline Instance 5  │
                     │                                             │
                     │  asyncpg pool  (min 2 / max 20 conns)      │
                     └────────────────────┬────────────────────────┘
                                          │
                                   ┌──────▼──────┐
                                   │  PostgreSQL  │
                                   │  + pgvector  │
                                   └─────────────┘

  Scaling beyond 5 calls:
  Multiple Docker containers ──► shared PostgreSQL (only shared resource)
```
