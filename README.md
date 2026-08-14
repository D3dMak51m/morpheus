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
- **A team that answers the objection actually raised** — the opener makes the first argument,
  then the swarm reads the discussion, **quotes the strongest line arguing against us** (verified
  to be present in the thread, so nobody answers an invented opponent) and picks a **technique**
  against that specific objection — correct it with a fact, move the criterion, concede and
  redirect, or ask what it rests on. The teammate who answers gets the same objection and a
  *different* technique, so three accounts are a discussion rather than an echo.
- **It goes and finds out what it doesn't know** — a score, a price, today's news are in neither
  the model's training data nor the corpus. Before answering, the swarm decides whether fresh data
  is needed, searches its own **SearXNG**, reads the pages and files them through the ordinary
  knowledge pipeline — so what one agent looked up, the whole swarm knows afterwards.
- **Caste hierarchy** — the cost tier, separate from the job: **alpha** (full pipeline),
  **beta** (cheap, "lite"), **gamma** (emoji reaction only).
- **Missions are teams with a lifecycle** — a mission moves **draft → recon → ready → active**, and
  it may **not** go active until reconnaissance has built its case file: no facts, no arguing. It
  states ONE claim (*our side*) plus the opponent, the arguments and the red lines, and separately
  what the audience should end up thinking. Its roster has **jobs, not volume levels** — scout
  (establish what is claimed), opener (first substantive argument), support (answer the objection
  actually raised), closer (de-escalate) — and shares **one memory**, so three accounts do not
  replay each other's argument in one thread. Reconnaissance names the mission's **subject** and,
  when the knowledge base holds nothing about it, searches the web for it instead of reporting a
  dead end.
- **Missions are measured** — for each discussion the swarm enters: the crowd's stance toward us
  before and after, whether the thread grew, and how many real people answered *us*. Tone is read
  over the replies **after** our entry, because one comment among twenty cannot move an average.
- **Knowledge base** — channels marked *news*, RSS feeds and web sources are ingested,
  LLM-classified, embedded and deduplicated into a pgvector RAG store the bots reason from. The
  scrapers open the **full article**, not the feed's announcement (measured: 4–44× more text),
  scrub site boilerplate, canonicalise place names across ru/en/uz so a channel can be grounded in
  its own region's news, and judge freshness by the source's publication date. Dead or
  unscrapeable sources are surfaced as `degraded` instead of failing silently.
- **Live observability** — a real-time **Live Ops** feed of every action, an interactive
  **swarm dashboard**, and a knowledge explorer.
- **Safety & realism** — Telegram FloodWait/ban handling, per-account cooldowns,
  per-channel/agent rate limits, proactive **target health** (uncommentable channels are
  surfaced and skipped), **active-hours** (bots post only in the persona's live hours, not
  24/7), and one-click pause for any agent or mission.
- **Simulation polygon** — a Telegram-like **isolated** test environment for personas,
  missions, RAG, system prompts, comments/reactions and mass generation. Own `sim_*` tables,
  own Redis queue, no production writes and no rate limits — tune an agent there, then apply
  it to a real soul. See **`SIMULATION.md`**.

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
| **SearXNG** | (8080 int.) | The swarm's own search front-end — how it finds what it doesn't know |
| **postgres** | 5432 | Relational store + **pgvector** (RAG embeddings) |
| **redis** | 6379 | Queues, telemetry stream, locks, cooldowns |

**External:** host **Ollama** (`host.docker.internal:11434`) serving `qwen2.5:3b`
(generation) and `nomic-embed-text` (embeddings). One small GPU, models run serially.

### Data flow
```
news channels ──> MYRMIDON ──┐
web search (SearXNG) ────────┴─> DAEDALUS /knowledge ──> classify+embed+dedup ──> knowledge_facts (RAG)
                                                                                          │
Mission (phase=active) ──> recon builds the dossier (searches the web if the base is empty) ┘
        └─> opener scans targets ──> ORPHEUS relevance ──> objection quoted ──> technique chosen
                                     (persona + RAG + memory + crowd mood + position + dossier
                                      + fresh lookup when the answer depends on something that changes)
                                                          │ posts via MYRMIDON
                                                          ├─> dialogue watch ──> human reply ──> ORPHEUS reply ──> answer (multi-turn)
                                                          └─> the team joins ──> support answers the objection, closer cools a hostile thread
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
3. **Миссии** — create a Mission: what the audience should end up thinking, the ONE claim it
   asserts (*our side*), the opponent, its arguments and red lines, target channels and a roster
   with jobs (scout / opener / support / closer). Then **run reconnaissance** — the mission cannot
   go active until it has facts, and if the knowledge base holds none it says which sources are
   missing. Watch the **Досье** and **Результат** tabs as it works.
4. Watch it work in **Live Ops** and **Рой** (swarm dashboard); review what it learned in
   **Знания роя**.

---

## Console screens

- **Live Ops** — near-real-time chronological feed of every agent action (poll, read,
  think, comment, reply, react, amplify, ingest) with a per-agent status rail.
- **Рой (Swarm Dashboard)** — comments/replies/reactions/knowledge by caste and agent;
  click any number to drill into the actual records.
- **Миссии** — permanent-goal missions: lifecycle phase (draft → recon → ready → active, with
  activation refused until the case file exists), the position, targets (+ approve agent-suggested
  ones), roster by job, the shared **dossier**, and per-discussion **results** (did the tone move,
  did anyone engage).
- **Souls / Агенты** — persona editor, pause/resume, account channel manager.
- **Знания роя** — the RAG knowledge base (search by text/source).
- **Профили каналов** — what the swarm knows about each channel: geo, topics, what's being
  discussed now, summary (drives in-context relevance + comment grounding).
- **Решения** — durable history of WHY a bot did/didn't react: what it recognized (text /
  audio transcript / photo OCR), the relevance verdict, and skip reasons.
- **Симуляция** — the isolated polygon: activity feed / channel posts ↔ single-post comment
  thread / channels + actions, with mass generation, knowledge import and landscape scraping.
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
SIMULATION.md   the isolated test polygon (entities, API, isolation contract)
```

Tests (both suites run inside the containers):

```bash
docker compose exec daedalus python -m pytest tests -q
docker compose exec orpheus  python -m pytest tests -q
```

---

## Status & roadmap

Done: cognitive comments (human, anti-repeat), conversations, memory, news→knowledge (RAG),
caste hierarchy, mission-driven engine, dynamic per-post tactic, agent-proposed targets,
reading photos & audio (STT + VLM + OCR), **Channel Profiling Phase 1** (posts judged in the
channel's topic/geo/hot-theme context, not in a vacuum), **relevance/tactic hardening Stage 36**
(concrete goal+entity anchored relevance with a keyword recall-override; target identifiers
canonicalised so a `t.me/…` URL is never a silent dead target; tactic heat = real insults not
just `!`; channel context grounds tone not topic; garbage-hashtag sanitiser), reliability,
full operator console. See `walkthrough.md` for the staged log.

**Stage 38 — relevance/RAG/publishing reliability — ✅**: the gate now judges whether a
persona can naturally join a post's **live discussion** (`ДА` / `СЛАБО` / `НЕТ`) after input
hygiene, selects the strongest candidate instead of the newest post, and uses an explicit
mission position. RAG retrieves a wider candidate set but admits facts by lexical overlap,
avoiding confident irrelevant context; stored HTML is scrubbed and backfillable. Targets are
health-checked, comment-disabled posts do not blacklist their channel, and
`CHAT_GUEST_SEND_FORBIDDEN` joins the discussion group then retries. Evidence and measurements:
`DIAGNOSIS.md`.

> **Operator note:** write a mission's **`stance` as a short argued claim**, not a tag list —
> the bot can only argue a position it can read as one (a `-ism` salad yields muddled rebuttals).

**DAEDALUS console redesign — ✅ COMPLETE** (Stages 65–77): the whole operator console is now a
professional command-and-control center on **Mantine 7** — `AppShell` layout, entity routing
(`#/<view>/<id>`), reusable `DataView`/`DetailPage`/`EntityPicker`/`StatTile`, **full-screen
master→detail editing of every entity** (no modals/drawers), **pick-from-list everywhere**, a serious
Dashboard, relationship cross-links, and h-scroll tables. See `DAEDALUS_CAPABILITIES.md` for the full
screen↔endpoint map and `walkthrough.md` for the staged log.

**Simulation polygon — ✅ built** (isolated test environment): `sim_*` schema on its own
declarative base, `/api/v1/simulation/*`, a dedicated `queue:sim_gen` ORPHEUS handler that
persists nothing, landscape scraping (RSS / web / public `t.me/s/` preview) and read-only
imports from production, a Telegram-like 3-column workspace, mass generation, and 60 tests.
See `SIMULATION.md`.

**Stage 46 — the team answers the objection, not the mood — ✅**: the functional roles finally
reach the live path (they had been prompt text production never sent), the swarm quotes the
objection actually raised and picks a technique against it, the shared dossier is really written
(it had silently recorded nothing), the pacing delay no longer blocks the whole queue, and the
engines read the mission **phase**, so "no case file, no fighting" holds for missions activated
before the rule existed.

**Stage 47 — the swarm can go and find out — ✅**: `searxng` + `daedalus/app/tools.py` give it
search and page reading, wired into reconnaissance (which now names the mission's subject with the
model, because IDF over a scattered corpus cannot) and into the publication path behind a
freshness gate. Everything read is filed, so a lookup improves the corpus permanently.

**Stage 48 — the whole system tested, audited and planned — ✅**: a full end-to-end pass (every
subsystem, a live publication to a real channel, the polygon, all 20 console screens) found and
fixed four defects in flight, including one deadlock that stopped the execution loop. Three audits
followed — `SYSTEM_STATE.md` (what works and what it costs), `FUNCTIONAL_GAPS.md` (what the swarm
cannot do, compared against Generative Agents / AgentSociety / OASIS / CrewAI) and `CODE_AUDIT.md`
(code quality, with `pyproject.toml` + `tools/check_architecture.py` added to enforce the rules).

**Current plan of record: `ROADMAP.md`** — foundation → personas → autonomy → technical debt.
Model replacement and hardware upgrades are postponed by the operator; UX/UI comes afterwards.
The text model (`qwen2.5:3b`) remains the ceiling on text quality: it invents facts and sometimes
emits fluent nonsense that no cheap guard can catch — lexical, statistical and language-identification
detectors were each measured and rejected. The prompts and guards are model-agnostic, so a larger
`TEXT_MODEL_NAME` is a drop-in improvement whenever the operator chooses.
