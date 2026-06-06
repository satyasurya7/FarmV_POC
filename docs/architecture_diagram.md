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
        SIP["SIP / WebSocket Gateway\nTwilio Media Streams protocol\nG.711 µ-law · 8 kHz"]
    end

    %% ─────────────────────────────────────────
    %% VOICE AGENT APPLICATION
    %% ─────────────────────────────────────────
    subgraph APP["Farm Vaidya Voice Agent  ·  FastAPI + Pipecat  ·  asyncio"]
        direction TB
        TRANSPORT["WebSocket Transport\n/ws/smartflo  ·  one session per call"]
        CALLERTAP["CallerTap\nCaptures caller audio\nGated by BotStarted/StoppedSpeaking"]
        STT["Soniox STT\nTelugu  ·  language = te"]
        STTG["STTGuardProcessor\nDrops empty / noise frames\nSpeaks Telugu canned response"]
        RAGPROC["RAGContextProcessor\nLast 3 turns → hybrid retrieval\nInjects KB context into system prompt"]
        LLM["Gemini 2.5 Flash\nVertex AI  ·  asia-south1 (Mumbai)\nFull conversation history per turn"]
        LLMLOG["LLMResponseLogger\nBuffers streamed text\nMeasures LLM + TTS latency\nlogs response + metrics to DB"]
        TTS["Cartesia TTS\nTelugu voice  ·  sonic-3"]
        BOTTAP["BotTap\nCaptures TTS audio\nWith wall-clock timestamps"]
        LLMERR["LLMErrorProcessor\nCatches ErrorFrame\nlogs to DB · continues"]
    end

    %% ─────────────────────────────────────────
    %% RECORDING
    %% ─────────────────────────────────────────
    subgraph REC["Call Recording (per session)"]
        RECORDER["ConversationRecorder\nasyncio Queue serialises writes\nBot audio always captured\nCaller audio gated (no echo)"]
        WAVFILES[("data/recordings/\n{session_id}_conversation.wav")]
    end

    %% ─────────────────────────────────────────
    %% RAG STACK
    %% ─────────────────────────────────────────
    subgraph RAG["RAG Stack"]
        direction TB
        EMBED["Vertex AI Embeddings\ntext-multilingual-embedding-002\n768-dim  ·  Telugu-native"]
        VEC["pgvector IVFFlat Index\nCosine Similarity  ·  lists = 50"]
        FTS["PostgreSQL FTS\ntsvector  ·  simple tokeniser"]
        MERGE["Hybrid Merge\nSemantic (priority) +\nFTS × 0.5  →  top-k chunks"]
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
    %% OBSERVABILITY + DASHBOARD
    %% ─────────────────────────────────────────
    subgraph OBS["Observability & Dashboard"]
        DBLOG["db_logger\nasync loggers"]
        HEALTH["/health\nliveness probe"]
        METRICS["/metrics\n24h call stats"]
        DASH["Dashboard UI\n/dashboard\nSession list · detail · recordings"]
        DASHAPI["Dashboard API\n/dashboard/api/sessions\n/dashboard/api/stats\n/dashboard/api/sessions/{id}/errors"]
    end

    %% ─────────────────────────────────────────
    %% MAIN CALL FLOW
    %% ─────────────────────────────────────────
    CALLER      -->|"PSTN / RTP audio"| SIP
    SIP         -->|"WebSocket JSON (µ-law → PCM)"| TRANSPORT
    TRANSPORT   --> CALLERTAP
    CALLERTAP   -->|"Speech frames"| STT
    STT         -->|"TranscriptionFrame"| STTG
    STTG        -->|"Valid utterance"| RAGPROC
    RAGPROC     -->|"Augmented system prompt"| LLM
    LLM         -->|"Streaming Telugu text"| LLMLOG
    LLMLOG      -->|"Text"| TTS
    TTS         -->|"PCM → µ-law → JSON"| BOTTAP
    BOTTAP      --> LLMERR
    LLMERR      -->|"Audio frames"| TRANSPORT
    TRANSPORT   -->|"Audio stream"| SIP
    SIP         -->|"Synthesised voice"| CALLER

    %% ─────────────────────────────────────────
    %% RECORDING FLOW
    %% ─────────────────────────────────────────
    CALLERTAP   -->|"caller audio"| RECORDER
    BOTTAP      -->|"bot audio + timestamp"| RECORDER
    RECORDER    --> WAVFILES
    WAVFILES    -. "served" .-> DASH

    %% ─────────────────────────────────────────
    %% RAG RETRIEVAL PATH
    %% ─────────────────────────────────────────
    RAGPROC     -->|"embed query"| EMBED
    EMBED       -->|"768-dim vector"| VEC
    RAGPROC     -->|"text query"| FTS
    VEC         --> MERGE
    FTS         --> MERGE
    MERGE       -->|"top-k KB chunks"| RAGPROC
    KC          -. "stored vectors" .-> VEC
    KC          -. "FTS index" .-> FTS

    %% ─────────────────────────────────────────
    %% LOGGING WRITES (all async, per turn)
    %% ─────────────────────────────────────────
    TRANSPORT   -->|"session_start / session_end"| DBLOG
    STTG        -->|"log_utterance"| DBLOG
    LLMLOG      -->|"log_response + log_metrics"| DBLOG
    RAGPROC     -->|"log_retrieval"| DBLOG
    LLMERR      -->|"log_error"| DBLOG

    DBLOG --> CS
    DBLOG --> UTT
    DBLOG --> AR
    DBLOG --> RL
    DBLOG --> EL
    DBLOG --> PM

    HEALTH      -. "ping" .-> DB
    METRICS     -. "query" .-> DB
    DASHAPI     -. "query" .-> DB
    DASH        --> DASHAPI
```

---

## 2. Pipeline Processor Order (per session)

```
transport.input()
  │
  ├─► CallerTap               ← captures caller audio; gates on BotStarted/StoppedSpeaking (upstream)
  │
  ├─► SonioxSTT               ← audio → Telugu text (TranscriptionFrame)
  │
  ├─► STTGuardProcessor       ← drops empty / noise frames; speaks canned Telugu response
  │
  ├─► LLMUserContextAggregator← accumulates conversation history
  │
  ├─► RAGContextProcessor     ← embeds last 3 turns; hybrid retrieval; injects KB context
  │
  ├─► Gemini 2.5 Flash        ← streams Telugu response text (LLMTextFrame)
  │
  ├─► LLMResponseLogger       ← buffers streamed text; measures LLM + TTS latency; logs to DB
  │
  ├─► CartesiaTTS              ← text → PCM audio (OutputAudioRawFrame)
  │
  ├─► BotTap                  ← captures TTS audio with wall-clock timestamps
  │
  ├─► LLMErrorProcessor       ← catches ErrorFrame; logs; continues pipeline
  │
  └─► LLMAssistantContextAggregator ← adds assistant turn to conversation history
  │
transport.output()
```

---

## 3. Call Sequence (Single Turn)

```mermaid
sequenceDiagram
    autonumber
    actor Farmer as Caller (Farmer)
    participant TT   as Tata Tele<br/>WebSocket Gateway
    participant WS   as Pipecat<br/>Transport
    participant REC  as ConversationRecorder
    participant STT  as Soniox STT<br/>(Telugu)
    participant RAG  as RAGContext<br/>Processor
    participant VX   as Vertex AI<br/>(Embeddings + LLM)
    participant LLM  as Gemini 2.5 Flash
    participant LOG  as LLMResponseLogger
    participant TTS  as Cartesia TTS<br/>(Telugu)
    participant DB   as PostgreSQL

    Farmer ->> TT:   Speaks in Telugu (audio)
    TT     ->> WS:   WebSocket JSON frames (µ-law)
    WS     ->> REC:  Caller audio (CallerTap — gated)
    WS     ->> STT:  PCM audio frames
    STT    ->> RAG:  TranscriptionFrame (Telugu text)
    RAG    ->> VX:   Embed last 2 turns (text-multilingual-embedding-002)
    VX     -->> RAG: 768-dim query vector
    RAG    ->> DB:   pgvector cosine search + FTS
    DB     -->> RAG: Top-k KB chunks
    RAG    ->> DB:   log_retrieval (query, chunks, score, latency)
    RAG    ->> LLM:  Augmented system prompt + full conversation history
    LLM    ->> VX:   Gemini 2.5 Flash inference (Vertex AI)
    VX     -->> LLM: Streaming Telugu text
    LLM    ->> LOG:  LLMTextFrame (streamed tokens)
    LOG    ->> TTS:  Buffered Telugu text
    TTS    -->> WS:  OutputAudioRawFrame (PCM)
    WS     ->> REC:  Bot audio (BotTap — timestamped)
    Note over LOG,WS: BotStartedSpeakingFrame travels upstream → LOG records TTS latency
    LOG    ->> DB:   log_response + log_metrics (retrieval/LLM/TTS/E2E ms)
    WS     ->> TT:   Audio stream (µ-law JSON)
    TT     ->> Farmer: Synthesised Telugu voice
    REC    ->> REC:  Writes {session_id}_conversation.wav
```

---

## 4. Component Summary

| Layer | Component | Technology |
|---|---|---|
| Telephony | Incoming / outgoing voice | Tata Tele Business Services — Twilio Media Streams over WebSocket |
| Transport | WebSocket session management | Pipecat `FastAPIWebsocketTransport` + `TataTeleSerializer` (µ-law ↔ PCM) |
| VAD | End-of-speech detection | Silero VAD + Smart Turn v3 (Pipecat built-in) |
| STT | Speech-to-text | Soniox — Telugu (`Language.TE`) |
| Recording | Per-call WAV capture | `ConversationRecorder` + `CallerTap` / `BotTap` → `data/recordings/` |
| RAG | Knowledge retrieval | pgvector IVFFlat (cosine) + PostgreSQL FTS — hybrid merge |
| Embeddings | Query + chunk vectorisation | Vertex AI `text-multilingual-embedding-002` (768-dim, Telugu-native) |
| LLM | Response generation | Gemini 2.5 Flash — Vertex AI `GoogleVertexLLMService`, `asia-south1` (Mumbai) |
| Latency logging | Response + metrics capture | `LLMResponseLogger` — measures LLM + TTS latency via `BotStartedSpeakingFrame` |
| TTS | Text-to-speech | Cartesia — `sonic-3`, Telugu voice ID from env |
| Database | Persistence + vector store | PostgreSQL 15 + pgvector extension |
| Logging | Async event capture | `db_logger` → 6 PostgreSQL tables |
| Dashboard | Observability web UI | FastAPI + Tailwind CSS — session list, detail view, audio playback, error log |
| Deployment | Containerisation | Podman Compose (app + postgres) + NGROK tunnel |

---

## 5. Database Schema

```mermaid
erDiagram
    call_sessions {
        uuid        session_id    PK
        text        phone_number
        text        caller_name
        text        tata_call_id
        text        status
        float       duration_s
        timestamptz started_at
        timestamptz ended_at
    }
    utterances {
        uuid        utterance_id  PK
        uuid        session_id    FK
        int         turn_number
        text        text
        float       confidence
        float       stt_latency_ms
        timestamptz created_at
    }
    agent_responses {
        uuid        response_id   PK
        uuid        utterance_id  FK
        uuid        session_id    FK
        int         turn_number
        text        text
        float       llm_latency_ms
        float       tts_latency_ms
        timestamptz created_at
    }
    retrieval_logs {
        uuid        retrieval_id       PK
        uuid        utterance_id       FK
        uuid        session_id         FK
        text        query
        jsonb       retrieved_chunks
        float       top_score
        float       retrieval_latency_ms
        timestamptz created_at
    }
    error_logs {
        uuid        error_id      PK
        uuid        session_id    FK
        text        error_type
        text        error_message
        text        stack_trace
        timestamptz created_at
    }
    performance_metrics {
        uuid        metric_id          PK
        uuid        session_id         FK
        int         turn_number
        float       stt_latency_ms
        float       retrieval_latency_ms
        float       llm_latency_ms
        float       tts_latency_ms
        float       e2e_latency_ms
        timestamptz created_at
    }
    knowledge_chunks {
        uuid        chunk_id      PK
        text        source_file
        text        question_number
        text        question
        text        content
        vector      embedding
        tsvector    ts_content
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

## 6. Concurrency Model

```
                     ┌──────────────────────────────────────────────────────────┐
                     │              FastAPI  (single process · asyncio)         │
                     │                                                          │
  Call 1 ─WebSocket─►│  asyncio Task 1  ──► Pipeline 1  ──► Recorder 1 → WAV │
  Call 2 ─WebSocket─►│  asyncio Task 2  ──► Pipeline 2  ──► Recorder 2 → WAV │
  Call 3 ─WebSocket─►│  asyncio Task 3  ──► Pipeline 3  ──► Recorder 3 → WAV │
  Call 4 ─WebSocket─►│  asyncio Task 4  ──► Pipeline 4  ──► Recorder 4 → WAV │
  Call 5 ─WebSocket─►│  asyncio Task 5  ──► Pipeline 5  ──► Recorder 5 → WAV │
                     │                                                          │
                     │  asyncpg pool  (min 2 / max 20 conns)                   │
                     └──────────────────────────┬───────────────────────────────┘
                                                │
                                  ┌─────────────▼──────────┐
                                  │      PostgreSQL         │
                                  │      + pgvector         │
                                  └─────────────────────────┘

  Session isolation:
    Each call has its own Pipeline instance, LLM context, RAGContextProcessor,
    ConversationRecorder, and asyncio Queue — zero shared mutable state.

  Scaling beyond 5 calls:
    Multiple Podman containers ──► shared PostgreSQL (only shared resource)
    Each container independently handles its own sessions and recordings.
```
