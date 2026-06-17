# MORPHEUS

**MORPHEUS** is an autonomous social-influence swarm for **Telegram**. Persona-driven
bots ("souls") read channels, write context-aware comments, hold multi-turn
conversations with real people, build a knowledge base from the news they read, and
coordinate as a caste hierarchy to advance **Missions** — permanent narrative goals.
A single operator runs everything from the **DAEDALUS** web console.

> ⚠️ Research/operational project. Telegram is the live platform; the mobile (Appium)
> path is currently non-functional and out of scope.

---

## Highlights

- **Cognitive comments** — each post is answered from the bot's persona + retrieved
  world-knowledge (RAG) + per-person memory + the live mood of the discussion, with
  anti-parroting and anti-repeat guards. Comments are short and human, not press-release.
- **Reads photos & audio** — a post's images (incl. albums) are described by a VLM and
  read with OCR (text cards), and its voice/audio is transcribed by the **HEIMDALL** STT
  service (any format, many languages) — so bots react to what they actually see and hear.
- **Real conversations** — after commenting, a bot watches for human replies and answers
  them, carrying a thread for several turns.
- **Caste hierarchy** — **alpha** (smartest, full pipeline) seeds; **beta** (cheap, "lite")
  reinforces; **gamma** (cheapest) just reacts with emoji. No bot acts identically.
- **Missions as permanent goals** — a mission has its own *stance/"truth"*, many target
  channels, and a roster. Its alpha seeds on relevant new posts, choosing a **tactic per
  post** (amplify / soft-support / displace / cunning sentiment-shift) from the thread's mood
  versus the stance; its beta/gamma amplify. Missions never "complete" — only **active** or
  **paused**.
- **Knowledge base** — channels marked *news* are ingested, LLM-classified, embedded and
  deduplicated into a pgvector RAG store the bots reason from.
- **Live observability** — a real-time **Live Ops** feed of every action, an interactive
  **swarm dashboard**, and a knowledge explorer.
- **Safety & realism** — Telegram FloodWait/ban handling, per-account cooldowns,
  per-channel/agent rate limits, **active-hours** (bots post only in the persona's live
  hours, not 24/7), and one-click pause for any agent or mission.

---

## Architecture

Microservices over Docker Compose, glued by Redis (queues/streams) and Postgres+pgvector.

| Service | Port | Role |
|---|---|---|
| **DAEDALUS** | 8000 | Control plane — FastAPI API + React SPA (the operator console) |
| **ORPHEUS** | 8001 | Cognitive core — Redis worker; LLM prompt assembly, RAG, guardrails |
| **MYRMIDON** | (8003 int.) | Execution swarm — Pyrogram (Telegram), the autonomous engines |
| **HEIMDALL** | 8004 | Speech-to-text service — faster-whisper (CPU), any audio format |
| **HUGINN** | — | Scrapers — RSS/web → knowledge (legacy TG scraper unused) |
| **MUNINN** | 8002 | Long-term dialog memory — embedded ChromaDB |
| **postgres** | 5432 | Relational store + **pgvector** (RAG embeddings) |
| **redis** | 6379 | Queues, telemetry stream, locks, cooldowns |

**External:** host **Ollama** (`host.docker.internal:11434`) serving `qwen2.5:3b`
(generation) and `nomic-embed-text` (embeddings). One small GPU, models run serially.

### Data flow
```
news channels ──> MYRMIDON ──> DAEDALUS /knowledge/ingest ──> classify+embed+dedup ──> knowledge_facts (RAG)
                                                                                          │
active Mission ──> alpha scans targets ──> ORPHEUS relevance (goal+stance) ──> comment ◄─┘
                                              (persona + RAG + memory + mood + stance)
                                                          │ posts via MYRMIDON
                                                          ├─> dialogue watch ──> human reply ──> ORPHEUS reply ──> answer (multi-turn)
                                                          └─> swarm amplify ──> beta (lite comment) + gamma (reaction)
```

For the full module/table/Redis map and engineering rules, see **`CLAUDE.md`**.

---

## Quick start

Prerequisites: Docker + Docker Compose; a host **Ollama** with the models pulled:
```bash
ollama pull qwen2.5:3b
ollama pull nomic-embed-text
```
Create `.env` (gitignored) with at least:
```
DB_USER=morpheus_admin
DB_PASSWORD=...
TG_API_ID=...            # Telegram api_id
TG_API_HASH=...          # Telegram api_hash
SUPERADMIN_USERNAME=morpheus
SUPERADMIN_PASSWORD=...   # console login
INTERNAL_API_TOKEN=morpheus-internal-sync-key
```
Run:
```bash
docker compose up -d --build
```
Open the console at **http://localhost:8000** and log in with `SUPERADMIN_USERNAME` /
`SUPERADMIN_PASSWORD`.

### Operator flow
1. **Auth Factory** — log a Telegram account in (api_id/hash + phone code) → stored as a
   Pyrogram session on a `souls_accounts` row.
2. **Souls / Агенты** — bind the account to a persona (caste alpha/beta/gamma), edit the
   persona, classify the account's channels (target/news), pause/resume.
3. **Mission Deck** — create a permanent Mission: its goal, its **stance/"truth"**, target
   channels, and a roster (manual or auto-assigned). Activate it.
4. Watch it work in **Live Ops** and **Рой** (swarm dashboard); review what it learned in
   **Знания роя**.

---

## Console screens

- **Live Ops** — near-real-time chronological feed of every agent action (poll, read,
  think, comment, reply, react, amplify, ingest) with a per-agent status rail.
- **Рой (Swarm Dashboard)** — comments/replies/reactions/knowledge by caste and agent;
  click any number to drill into the actual records.
- **Mission Deck** — permanent-goal missions: stance, targets (+ approve agent-suggested
  ones), roster, pause/resume.
- **Souls / Агенты** — persona editor, pause/resume, account channel manager.
- **Знания роя** — the RAG knowledge base (search by text/source).
- **Профили каналов** — what the swarm knows about each channel: geo, topics, what's being
  discussed now, summary (drives in-context relevance + comment grounding).
- **Решения** — durable history of WHY a bot did/didn't react: what it recognized (text /
  audio transcript / photo OCR), the relevance verdict, and skip reasons.
- Accounts, Auth Factory, Clone Factory, Landscape, Scouting Radar, Devices, etc.

---

## Project layout

```
daedalus/   control plane (FastAPI app/ + React frontend/)
orpheus/    cognitive core (Redis worker)
myrmidon/   execution swarm (Pyrogram drivers + autonomous engines)
huginn/     scrapers
muninn/     dialog memory (ChromaDB)
docker-compose.yml
CLAUDE.md       engineering guide (read this to work on the code)
walkthrough.md  work log / handoff / next steps
```

---

## Status & roadmap

Done: cognitive comments (human, anti-repeat), conversations, memory, news→knowledge (RAG),
caste hierarchy, mission-driven engine, dynamic per-post tactic, agent-proposed targets,
reading photos & audio (STT + VLM + OCR), **Channel Profiling Phase 1** (posts judged in the
channel's topic/geo/hot-theme context, not in a vacuum), reliability, full operator console.
See `walkthrough.md` for the staged log.

Next: **DAEDALUS console UX overhaul** (in progress) — bringing the whole operator console to a
"mission center" standard. Phase 1 (foundation): hash routing, a reusable `DataTable`, Database
Explorer fix. Phase 2 (uniform lists): migrating the list screens to `DataTable` for consistent
search/sort/pagination — done across `DecisionLog`, `AccountsManager`, `ChannelProfiles`,
`Landscape`, `ScoutingRadar`, `NewsHub`, and the `Рой` drill-downs (`MuninnExplorer` keeps its own
server-side search; `Devices` stays a control dashboard). Next: de-modal editing,
styling/sliders, and screen consolidation. The text model is small (`qwen2.5:3b`); a larger
`TEXT_MODEL_NAME` sharpens comments/relevance — the prompts and guards are model-agnostic.
