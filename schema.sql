-- Farm Vaidya Telugu Voice Agent — PostgreSQL Schema
-- Run once on a fresh database (auto-run by Docker entrypoint)

CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- for trigram / keyword search

-- ─── Call sessions ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS call_sessions (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    phone_number    VARCHAR(20),
    tata_call_id    VARCHAR(100),
    caller_name     VARCHAR(200),
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at        TIMESTAMPTZ,
    duration_s      FLOAT,
    status          VARCHAR(20) NOT NULL DEFAULT 'active',   -- active | completed | error | dropped
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── User utterances (STT output) ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS utterances (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID        NOT NULL REFERENCES call_sessions(id) ON DELETE CASCADE,
    turn_number     INTEGER     NOT NULL,
    text            TEXT        NOT NULL,
    confidence      FLOAT,
    stt_latency_ms  FLOAT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── Agent responses (LLM → TTS) ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_responses (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID        NOT NULL REFERENCES call_sessions(id) ON DELETE CASCADE,
    utterance_id    UUID        REFERENCES utterances(id),
    turn_number     INTEGER     NOT NULL,
    text            TEXT        NOT NULL,
    llm_latency_ms  FLOAT,
    tts_latency_ms  FLOAT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── RAG retrieval logs ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS retrieval_logs (
    id                  UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id          UUID    NOT NULL REFERENCES call_sessions(id) ON DELETE CASCADE,
    utterance_id        UUID    REFERENCES utterances(id),
    query               TEXT    NOT NULL,
    retrieved_chunks    JSONB,               -- [{chunk_id, text, score}, ...]
    top_score           FLOAT,
    retrieval_latency_ms FLOAT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── Error logs ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS error_logs (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID        REFERENCES call_sessions(id) ON DELETE SET NULL,
    error_type      VARCHAR(50),  -- stt_failure | llm_timeout | tts_error | retrieval_error | network
    error_message   TEXT,
    stack_trace     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── Per-turn performance metrics ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS performance_metrics (
    id                      UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id              UUID    NOT NULL REFERENCES call_sessions(id) ON DELETE CASCADE,
    turn_number             INTEGER,
    stt_latency_ms          FLOAT,
    retrieval_latency_ms    FLOAT,
    llm_latency_ms          FLOAT,
    tts_latency_ms          FLOAT,
    e2e_latency_ms          FLOAT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── Knowledge base chunks (RAG vector store) ─────────────────────────────────
-- embedding dim = 768 for text-multilingual-embedding-002
CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    source_file     VARCHAR(255),
    chunk_index     INTEGER,
    content         TEXT        NOT NULL,
    metadata        JSONB,                   -- crop_name, variety_code, category, etc.
    embedding       vector(768),
    ts_content      TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── Indexes ──────────────────────────────────────────────────────────────────

-- Vector similarity search (IVFFlat — rebuild after bulk ingest)
CREATE INDEX IF NOT EXISTS idx_chunks_embedding
    ON knowledge_chunks USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 50);

-- Full-text search
CREATE INDEX IF NOT EXISTS idx_chunks_ts
    ON knowledge_chunks USING GIN (ts_content);

-- Trigram search (crop codes / variety names)
CREATE INDEX IF NOT EXISTS idx_chunks_trgm
    ON knowledge_chunks USING GIN (content gin_trgm_ops);

-- FK indexes
CREATE INDEX IF NOT EXISTS idx_utterances_session    ON utterances(session_id);
CREATE INDEX IF NOT EXISTS idx_responses_session     ON agent_responses(session_id);
CREATE INDEX IF NOT EXISTS idx_retrieval_session     ON retrieval_logs(session_id);
CREATE INDEX IF NOT EXISTS idx_errors_session        ON error_logs(session_id);
CREATE INDEX IF NOT EXISTS idx_metrics_session       ON performance_metrics(session_id);
