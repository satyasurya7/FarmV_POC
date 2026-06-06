# Technical Notes — Farm Vaidya Telugu Voice Agent

## 1. Chunking Strategy

**File type:** The knowledge base (`rythunestam_kb_v1.4.docx`) is a Word document containing **2,307 Q&A pairs** in Telugu. There are no tables — all content is in paragraphs.

**Approach:** Q&A pair extraction  
Each numbered question (e.g., `1.2. ఈ స్థితి ఎందుకు వచ్చింది సార్?`) plus its following answer paragraph(s) forms one chunk. This is detected by a regex on the paragraph prefix (`^\d+(\.\d+)+[\.\s]`).

**Why this works better than sliding window:**  
- Each chunk is semantically self-contained (question + answer)  
- Embeddings carry both the question phrasing and the answer content, so queries match both ways  
- No answer is split across two chunks  
- Average chunk size: ~150–300 tokens — well within embedding model limits

**Metadata per chunk:** `question_number`, `question` (first 200 chars), `source_file`

**For CSV/text fallback:** 300-token sliding window with 50-token overlap (tiktoken `cl100k_base`).

---

## 2. Embedding Model

**Model:** `text-multilingual-embedding-002` (Google Vertex AI)  
**Dimensions:** 768  
**Languages:** Supports Telugu (te) natively  
**Endpoint:** Vertex AI — `asia-south1` (Mumbai region, same as LLM — minimises latency)

**Batching:** 250 texts per Vertex AI call (API limit), with exponential backoff retry (3 attempts).

---

## 3. Retrieval Approach

**Hybrid search** — two-stage merge:

1. **Semantic search** (primary): pgvector IVFFlat cosine similarity  
   `1 - (embedding <=> query_vector) >= threshold`  
   Returns top-k by similarity score.

2. **Full-text search** (supplementary): PostgreSQL `tsvector` + `plainto_tsquery('simple', query)`  
   Fills remaining top-k slots for results the semantic search missed (exact crop codes, variety names like "BPT 2537").

3. **Merge:** Semantic results take priority; FTS scores are normalised (×0.5) before merge.

**Why hybrid:** Crop variety codes (e.g., "BPT 2537", "WGL 32170") are short exact strings — embedding similarity alone may not rank them well. FTS ensures exact matches surface even if the vector distance is borderline.

**Vector index:** IVFFlat with `lists=50` (appropriate for ~2,300 vectors). Rebuild index after bulk ingest for optimal recall.

---

## 4. Session Management

Each incoming WebSocket connection (= one phone call) creates:
- A UUID `session_id` — primary key for all DB logging
- An independent Pipecat `Pipeline` and `PipelineTask` instance
- An independent `LLMMessagesFrame` history (multi-turn context)
- An independent `RAGContextProcessor` tracking turn number and utterance ID

**Context across turns:** Pipecat accumulates the conversation in `LLMMessagesFrame` (a list of `{role, content}` messages). The system prompt is refreshed with new RAG context on each turn; prior turns stay in the message history so the LLM can resolve references like "its yield" back to a previously mentioned crop variety.

**Conversational resolution example:**  
```
Turn 1: User: "BPT 2537 crop duration entha?"
        → RAG retrieves BPT 2537 details → LLM answers "165 days"
Turn 2: User: "Yield entha vastundi?"
        → RAG retrieves same context (query now includes conversation history via reformulation)
        → LLM has Turn 1 in message history → resolves "yield" to BPT 2537
```

---

## 5. Concurrency Strategy

**Model:** One asyncio task per call, all running in a single Python process.

**Why single process:**  
- Pipecat is built on asyncio — pipeline processors are coroutines  
- All I/O (STT, LLM, TTS, DB) is async — no thread blocking  
- For 5 concurrent calls, a single event loop is more than sufficient  
- No shared mutable state between sessions — each session has its own pipeline instance

**DB pool:** asyncpg pool with `min_size=2, max_size=20` — handles 20 concurrent DB operations.

**Scaling beyond 5 calls:** Run multiple Docker containers behind a load balancer; each container handles its own sessions independently. The database is the only shared resource (and it's connection-pooled).

---

## 6. Language Configuration

| Component | Language Setting |
|---|---|
| Soniox STT | `language="te"` (ISO 639-1 Telugu) |
| Cartesia TTS | `language="te"`, model `sonic-multilingual` |
| LLM System Prompt | Instructs Gemini to respond only in Telugu |
| Embeddings | `text-multilingual-embedding-002` — natively multilingual |
| FTS | `plainto_tsquery('simple', ...)` — language-agnostic tokenisation |

---

## 7. Error Handling Summary

| Failure Mode | Handler | User Experience |
|---|---|---|
| Empty STT output | `STTGuardProcessor` drops frame, speaks canned "please repeat" | Smooth — caller hears Telugu prompt |
| LLM API error | `LLMErrorProcessor` catches `ErrorFrame`, logs to DB, speaks Telugu fallback | Caller hears "సాంకేతిక సమస్య వచ్చింది" |
| Empty RAG results | `NO_CONTEXT_RESPONSE` injected into system prompt | Telugu "I don't have that information" |
| Embedding API timeout | `tenacity` retry 3× with exponential backoff | Transparent retry |
| WebSocket disconnect | Caught in `server.py`, session marked "dropped" | Clean DB state |
| Unhandled pipeline error | Logged, session marked "error", pipeline exits | No cross-session impact |
