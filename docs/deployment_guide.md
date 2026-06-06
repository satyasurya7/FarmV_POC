# Deployment Guide — Farm Vaidya Telugu Voice Agent

**Environment:** Fedora Linux · Podman · NGROK

---

## 1. System Setup (Fedora)

```bash
# Install Podman and podman-compose
sudo dnf install -y podman podman-compose

# Verify
podman --version       # Podman 4.x+
podman-compose --version

# Install NGROK
# Option A: Official installer
curl -sSL https://ngrok-agent.s3.amazonaws.com/ngrok.asc | sudo tee /etc/yum.repos.d/ngrok.repo
sudo dnf install ngrok

# Option B: Direct binary
wget https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz
tar xzf ngrok-v3-stable-linux-amd64.tgz
sudo mv ngrok /usr/local/bin/
```

---

## 2. Repository Setup

```bash
git clone <repo-url>
cd FarmV-POC
```

---

## 3. Credentials

### 3.1 Environment file

```bash
cp .env.example .env
```

Edit `.env` and fill in:

```env
# PostgreSQL (leave as-is for container setup)
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=farmvaidya
POSTGRES_USER=farmvaidya
POSTGRES_PASSWORD=<strong-password>

# Vertex AI
GOOGLE_CLOUD_PROJECT=<your-gcp-project-id>
GOOGLE_CLOUD_REGION=asia-south1
GOOGLE_APPLICATION_CREDENTIALS=/app/credentials/vertex-ai-project-494805-0340a8a815b9.json

# LLM
LLM_MODEL=gemini-2.5-flash
LLM_MAX_TOKENS=512
LLM_TEMPERATURE=0.1

# Soniox STT
SONIOX_API_KEY=<soniox-key>

# Cartesia TTS
CARTESIA_API_KEY=<cartesia-key>
CARTESIA_TELUGU_VOICE_ID=<telugu-voice-id>

# Tata Tele
TATA_TELE_API_KEY=<tata-key>
TATA_TELE_ENDPOINT=wss://<tata-tele-endpoint>
TATA_TELE_DID_NUMBER=+91XXXXXXXXXX

# RAG
RAG_TOP_K=5
RAG_SIMILARITY_THRESHOLD=0.65

# Server
SERVER_HOST=0.0.0.0
SERVER_PORT=8765
LOG_LEVEL=INFO
```

### 3.2 Vertex AI service account

```bash
mkdir -p credentials
cp /path/to/downloaded-sa.json credentials/vertex-ai-project-494805-0340a8a815b9.json
chmod 600 credentials/vertex-ai-project-494805-0340a8a815b9.json
```

### 3.3 Knowledge base files

```bash
mkdir -p knowledge_base
cp /path/to/rythunestam_kb_v1.4.docx knowledge_base/
```

---

## 4. Start Containers

```bash
# Build and start (first time — builds the image)
podman compose up -d --build

# Subsequent starts (no rebuild)
podman compose up -d

# Watch startup logs — wait for "Server ready on port 8080"
podman compose logs -f voice_agent
```

### Expected startup sequence

```
farmvaidya_postgres  | database system is ready to accept connections
farmvaidya_agent     | Pre-loading VAD and Smart Turn models...
farmvaidya_agent     | Models pre-loaded
farmvaidya_agent     | Warming up Vertex AI embedding API...
farmvaidya_agent     | Vertex AI embedding warmed up. Server ready on port 8080
```

### Troubleshooting startup

| Symptom | Cause | Fix |
|---|---|---|
| `permission denied` on volume mount | SELinux denying access | Volumes in `compose.yml` already have `:z` label — check `podman compose` version |
| `pg_isready` fails | Postgres not ready | Wait 30s and retry; check `podman logs farmvaidya_postgres` |
| `google.auth.exceptions.DefaultCredentialsError` | Wrong credentials path | Verify the JSON filename matches `GOOGLE_APPLICATION_CREDENTIALS` |
| `Connection refused` on port 8080 | Agent not started | Check `podman compose ps` and agent logs |

---

## 5. Knowledge Base Ingest

Run once after first startup (or whenever the KB is updated):

```bash
podman exec farmvaidya_agent python scripts/ingest_kb.py
```

Expected output:

```
Reading knowledge_base/rythunestam_kb_v1.4.docx ...
Extracted 2307 Q&A chunks
Embedding batch 1/10 (250 chunks)...
...
Ingested 2307 chunks into pgvector. Index rebuild triggered.
```

Verify:

```bash
podman exec -it farmvaidya_postgres psql -U farmvaidya -d farmvaidya \
  -c "SELECT COUNT(*) FROM knowledge_chunks;"
# → 2307
```

---

## 6. NGROK Tunnel

### 6.1 Authenticate (once)

```bash
ngrok config add-authtoken YOUR_NGROK_AUTHTOKEN
```

### 6.2 Update ngrok.yml

Edit `ngrok.yml` and replace `YOUR_NGROK_AUTHTOKEN` with your actual token (or leave it out if you've already run `ngrok config add-authtoken`).

### 6.3 Start the tunnel

```bash
# In a dedicated terminal (or use tmux/screen)
ngrok start --config ngrok.yml farmvaidya
```

NGROK output shows:
```
Forwarding    https://abc123.ngrok.io -> http://localhost:8765
```

> **Free tier:** The URL changes every session. For a stable URL, use an NGROK paid plan with a fixed domain and uncomment the `domain:` line in `ngrok.yml`.

### 6.4 WebSocket URL for Tata Tele

```
wss://abc123.ngrok.io/ws/smartflo
```

Update this URL in the Tata Tele SmartFlo portal whenever NGROK restarts (on free tier).

### 6.5 NGROK inspector

Open `http://localhost:4040` in a browser to inspect all WebSocket frames — useful for debugging Tata Tele connectivity.

---

## 7. Tata Tele SmartFlo Configuration

1. Log in to the Tata Tele SmartFlo portal
2. Navigate to your DID number → **Incoming Call Settings**
3. Set **Media Stream URL** (WebSocket Callback):
   ```
   wss://<ngrok-url>/ws/smartflo
   ```
4. Enable headers:
   - `X-Caller-ID` — caller's phone number
   - `X-Call-ID` — Tata Tele call reference ID
5. Audio format: **PCM 16-bit, 8 kHz** (µ-law / G.711 if Tata Tele requires it — the transport handles the format)

---

## 8. Health Checks

```bash
# Agent liveness
curl http://localhost:8765/health
# → {"status":"ok","active_sessions":0}

# 24-hour stats
curl http://localhost:8765/metrics
# → {"active_calls":0,"completed_calls":5,"error_calls":0,"avg_duration_s":47.2}

# Container status
podman compose ps
```

---

## 9. Log Access

```bash
# Live agent logs
podman compose logs -f voice_agent

# Postgres query — last 10 calls
podman exec -it farmvaidya_postgres psql -U farmvaidya -d farmvaidya -c "
  SELECT session_id, phone_number, status, duration_s, turn_count, started_at
  FROM call_sessions
  ORDER BY started_at DESC
  LIMIT 10;
"

# Per-turn latency breakdown
podman exec -it farmvaidya_postgres psql -U farmvaidya -d farmvaidya -c "
  SELECT session_id, turn_number, stt_ms, retrieval_ms, llm_ms, tts_ms, e2e_ms
  FROM performance_metrics
  ORDER BY created_at DESC
  LIMIT 20;
"

# Error log
podman exec -it farmvaidya_postgres psql -U farmvaidya -d farmvaidya -c "
  SELECT error_type, message, created_at
  FROM error_logs
  ORDER BY created_at DESC
  LIMIT 10;
"
```

---

## 10. Updating the Agent

After a code change:

```bash
# Rebuild and restart only the agent container
podman compose build voice_agent
podman compose up -d voice_agent

# NGROK tunnel stays up — no URL change needed
```

---

## 11. Teardown

```bash
# Stop containers (keeps database data)
podman compose down

# Stop and delete all data (fresh start)
podman compose down -v

# Remove built image
podman rmi farmv-poc_voice_agent
```

---

## 12. Podman-Specific Notes

| Topic | Detail |
|---|---|
| SELinux | Bind mounts in `compose.yml` use `:z` label — required on Fedora. Named volumes (`postgres_data`) don't need it. |
| Rootless | Podman runs rootless by default. No `sudo` needed for `podman compose`. |
| Networking | Podman creates a bridge network (`farmv-poc_default`). Service names (`postgres`) resolve as DNS within the network. |
| `restart: unless-stopped` | Supported by `podman-compose` for the session. For persistent auto-restart across reboots, generate a systemd unit: `podman generate systemd --name farmvaidya_agent > ~/.config/systemd/user/farmvaidya-agent.service && systemctl --user enable --now farmvaidya-agent` |
