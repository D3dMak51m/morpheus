# CLAUDE.md — MORPHEUS project guide

This file orients a fresh session. Read it fully before working. Companion docs:
**`README.md`** (what the system is, how to run it) and **`walkthrough.md`** (work
log, current state, next steps). Keep all three current — see "Working rules".

---

## What MORPHEUS is

An autonomous social-influence swarm operating on **Telegram**. Persona-driven bots
("souls") read channels, comment on posts, hold multi-turn conversations with real
humans, gather news into a knowledge base, and coordinate as a caste hierarchy to
push **Missions** (permanent narrative goals). One operator drives it from the
**DAEDALUS** web console.

**Working platform = Telegram** (Pyrogram MTProto, real userbot accounts).
The mobile/Appium path (Instagram/Threads/YouTube) is **broken and out of scope**
(host ADB at `host.docker.internal:5037` refused; AVD orchestrator builds invalid
container names). Do not rabbit-hole on it unless explicitly asked.

---

## Containers (docker-compose.yml)

| Service | Image/Build | Port | Role |
|---|---|---|---|
| **postgres** | pgvector/pgvector:pg16 | 5432 | Relational store **+ pgvector** (RAG embeddings) |
| **redis** | redis:7-alpine | 6379 | Queues, telemetry stream, locks, cooldowns |
| **muninn** | ./muninn | 8002 | Long-term **dialog memory** (embedded ChromaDB, FastAPI) |
| **daedalus** | ./daedalus | 8000 | **Control plane**: FastAPI API + React SPA (built into the image) |
| **orpheus** | ./orpheus | 8001 | **Cognitive core**: Redis worker, calls host Ollama (GPU) |
| **huginn** | ./huginn | — | Legacy scrapers (RSS/web feed knowledge; **TG scraper is dead Telethon**) |
| **myrmidon** | ./myrmidon | (8003 internal) | **Execution swarm**: Pyrogram (TG) + Appium (mobile, broken) |
| **heimdall** | ./heimdall | 8004 | **Speech-to-text** service (faster-whisper, CPU/int8, any format) |

**External dependency:** host **Ollama** at `host.docker.internal:11434` — models
`qwen2.5:3b` (text generation), `nomic-embed-text` (RAG embeddings), and `moondream`
(VLM, image descriptions). MUNINN uses `intfloat/multilingual-e5-small` on CPU; HEIMDALL
runs faster-whisper on CPU (`STT_MODEL`, default `medium`; set `large-v3` in prod);
MYRMIDON has Tesseract (OCR for text-card images). The single ~6 GB GPU runs one model at
a time (ORPHEUS unloads with `keep_alive=0`); keep LLM calls cheap/serial.

Deploy a change: `docker compose build <svc> && docker compose up -d <svc>`.
The **React SPA is built inside the daedalus image** (`npm run build` → `app/static/`),
so any frontend change needs `docker compose build daedalus`.

---

## Services & key modules

### DAEDALUS (`daedalus/app/`) — control plane (FastAPI + React)
- `main.py` lifespan: `init_tables()` (create_all + idempotent column migrations in
  `database.py`), starts the mission DAG reconciler (legacy, dormant for new missions),
  mounts routers, serves the SPA (`spa_fallback`). Auth: JWT, `POST /api/v1/auth/login`.
- `models.py` — all ORM tables. `database.py` — engine + `init_tables` + the
  `_STAGE23_COLUMNS` migration block (add new columns to existing tables here; create_all
  never ALTERs).
- `rbac.py` — roles/permissions, `require_permission`. `souls.py` — agent profiles +
  accounts + bind/unbind + channel prefs (`agent_channel_prefs`) + channel enumeration
  proxy. `router_missions.py` — permanent-goal missions + targets + squad + **internal
  `/missions/internal/suggest-target`** (agents propose targets, dedup). `mission_control.py`
  — squad eligibility/auto-assign + `reconcile_dynamic_rosters` (the reconciler auto-fills
  `agent_mode='dynamic'` mission rosters to `dynamic_count` at runtime); DAG launch is legacy. `analytics.py` — metrics,
  `/stream` (durable activity), `/live` (telemetry tail), `/swarm` (dashboard aggregate),
  `/dialogues`. `router_knowledge.py` — RAG facts ingest/search/list + `/knowledge/internal/
  by-geo` (recent facts by PLACE = `tags` overlap, for channel comment grounding). `landscape.py` —
  scraping sources. `router_scouting.py`, `router_auth_factory.py` (TG login/code),
  `router_factory.py` (clone factory), `router_sandbox.py`, `db_explorer.py`,
  `classifier.py`/`embeddings.py` (LLM classify + embed for knowledge), `genesis_engine.py`.
  `channel_profiler.py` (LLM strict-JSON per-channel profile + hot themes) +
  `router_channels.py` (internal `/channels/internal/{profile,themes}` build + `…/profile`
  GET; operator `GET /channels/profiles` for the UI). `router_decisions.py` (internal
  `/decisions/internal/log`; operator `GET /decisions` — the durable decision history).
- React (`daedalus/frontend/src/`): **hash routing** — `App.tsx` keeps the active view in the
  URL hash (`#/view`) so refresh/deep-links/back-forward work. **`components/DataTable.tsx`** —
  reusable table (search / sortable columns / pagination / states); migrate list screens to it.
  `db_explorer` validates table reads against LIVE tables (not a stale whitelist). Components
  (`components/`): `LiveOps` (live activity), `SwarmDashboard`
  ("Рой", interactive drill-down), `SoulsContext` ("Агенты" editor: pause/resume, channels),
  `ChannelManager` (account channels), `MissionDeck` (missions), `MuninnExplorer`
  ("Знания роя", RAG facts), `ChannelProfiles` ("Профили каналов" — per-channel geo/topics/
  hot-themes), `DecisionLog` ("Решения" — durable why-did/didn't-react history),
  `LandscapeManager`, `ScoutingRadar`, `AccountsManager`,
  `AuthFactory`, etc. Operator-facing screens are **in Russian**.

### ORPHEUS (`orpheus/app/`) — cognitive core (NO HTTP for generation)
- `main.py` — Redis worker, multi-key `BRPOP` on `queue:raw_events` + `queue:mission_gen`.
  `handle_mission_generation` (mode=comment|reply, lite for beta; `_resolve_dynamic_tactic`
  picks the per-post tactic; anti-repeat via `_recent_outputs`/`_remember_output` +
  `guardrails.is_repeat`), `handle_relevance` (mode=relevance, mission- or profile-aware
  YES/NO; `_channel_context` weaves the **channel profile** — geo/topics/hot-themes — so a
  post is judged IN context, not in a vacuum).
  **`generate_text(prompt, max_tokens, temperature, penalties)`** — set
  `penalties=False` for short CLASSIFICATION calls (relevance, tactic): the anti-parroting
  repeat/frequency penalties otherwise push the model OFF the clean `да`/`нет` tokens (gave
  garbled `'дятьнет'` — the real cause of "non-deterministic relevance").
- `persona.py` — `PersonaEngine`: profile cache (30s poll of `/souls/internal/profiles`),
  `assemble_mission_prompt` (persona + RAG + MUNINN memory + thread mood + **media context** +
  **channel context** (`build_channel_block` — the channel's geo/topics/hot-themes + current
  region news by place) + mission
  stance + role/tactic; 4 `tactic_directives`; human-style + anti-repeat prompt blocks;
  **lite** branch for cheap beta inherits the tactic + channel context),
  `build_mood_prompt`/`tactic_from_mood` (dynamic per-post tactic = 3-way mood classify +
  heat heuristic), `fetch_memory`/`save_memory` (MUNINN).
- `rag.py` — `fetch_fresh_context` (pgvector knowledge retrieval). `guardrails.py` —
  output validation + **`is_echo`** (anti-parroting the input) + **`is_repeat`** (anti-rehashing
  the agent's OWN recent comments). `coordination.py` — legacy DAG beta amplification (the live
  swarm amplification is now in MYRMIDON `swarm.py`). `media_enricher.py` — VLM (Ollama) + STT
  **delegated to HEIMDALL** (no local Whisper). `telemetry.py` — `emit()` → `stream:agent_events`.

### MYRMIDON (`myrmidon/app/`) — execution swarm (Pyrogram)
- `main.py` — consumes `queue:execution_tasks`; `_execute_telegram` (comment via
  `text_provider`→ORPHEUS, or `action_type=react`); starts `dialogue_engine`,
  `target_engine`; respects account cooldown.
- `drivers/tg_client.py` — `TelegramDriver` (all TG ops): `execute_comment` (channel
  comment in the linked discussion group; reads post text + thread mood + **media context**),
  `execute_reaction` (gamma), `fetch_new_posts` (now keeps **media-only posts** via
  `has_media`), `_download_media`/`_read_media_context`/`read_media_context` (album-aware
  photo+audio download → enrich), `list_channels`, `run_dialogue_cycle`, `_flood_retry`.
  **`_run(coro)`**: fresh event loop per call (fixes daemon-thread loop crashes). Per-agent
  **session lock**.
- `media_reader.py` — turns a post's media into text: audio → **HEIMDALL** STT, image →
  Ollama VLM (Moondream) + **Tesseract OCR** (a text-card image is read by OCR; the small VLM
  hallucinates a scene on those). Returns a compact `media_context`.
- `target_engine.py` — **primary driver**: per active mission it first
  `_profile_channels_for_mission` (hybrid-cadence channel profiling, Redis-gated, runs BEFORE
  commenting), then the roster alpha scans the target channels, LLM-relevance-checks newest
  posts vs the mission's goal/stance **in the channel's profile context** (`_get_channel_profile`;
  caption-less media posts are "read" first so relevance is grounded in the photo/audio), seeds
  a comment (then `swarm.py` amplifies). Emits `media_read`/`relevance`/`rate_skip` to Live Ops.
  Also `_suggest_targets_for_mission` (bots propose new mission targets) and per-agent **news
  ingest** (role=news → DAEDALUS knowledge).
- `dialogue_engine.py` + `dialogue_store.py` — poll watched comments for human replies →
  ORPHEUS reply → post → register follow-up watch (multi-turn). Logs `action_type=reply`.
- `swarm.py` — caste amplification: after an **alpha** seed posts, **beta** = cheap "lite"
  comment, **gamma** = emoji reaction; companions scoped to the mission roster.
- `account_health.py` — FloodWait/ban classification, `mark_account` (ban→suspend profile),
  cooldowns. `schedule.py` — **active-hours gate** (`in_active_hours`: persona window in the
  swarm tz, `ACTIVE_HOURS_UTC_OFFSET` default +5; gates seeding/amplify/replies, fail-open).
  `telemetry.py` — same as ORPHEUS. `device_api.py` (8003), `adb_supervisor.py`,
  `avd_orchestrator.py`, `proxy_manager.py`, `sms_gateway.py`, `registration_driver.py`.

### HUGINN (`huginn/app/`) — scrapers
- `main.py` syncs `scraping_landscape` targets; runs RSS/web scrapers → knowledge.
  **`scrapers/tg_scraper.py` uses an unlogged-in Telethon session and is dead** — TG news is
  handled by MYRMIDON `target_engine` (Pyrogram) instead. `knowledge_ingest.py` POSTs to
  DAEDALUS `/knowledge/internal/ingest`.

### MUNINN (`muninn/app/main.py`)
- ChromaDB-backed semantic dialog memory. `POST /api/v1/memory/{search,save}` keyed by
  `agent_id` + `opponent_id`.

### HEIMDALL (`heimdall/app/main.py`) — speech-to-text service
- Own container, FastAPI. `POST /api/v1/transcribe` (multipart, any audio format via
  PyAV + ffmpeg fallback; optional `language` force) → `{text, language, …}`. faster-whisper
  on **CPU/int8**; `STT_MODEL` env (default `medium`; `large-v3` in prod) cached in the
  `heimdall_models` volume. Called by MYRMIDON `media_reader` and ORPHEUS `media_enricher`.

---

## Data model (Postgres)

RBAC: `admin_users`, `roles`, `role_permissions`, `user_roles`.
Identity: **`agent_profiles`** (persona; `caste` alpha|beta|gamma, `status` active|suspended|
unbound, `core_mission`, `core_interests`, `context_subscriptions`), **`souls_accounts`**
(access: platform, `auth_cookies` JSONB = `{session_string}`, `status` active|banned|limited|
unbound), `profile_history`.
Channels: **`agent_channel_prefs`** (per-agent channel classification role target|news|ignored
+ cached enumeration), **`channel_profiles`** (per-channel, NOT per-agent: `geo_layers`
[same closed set as knowledge], `geo_label`, `topics`, `recent_themes`, `summary` — built by
`channel_profiler`, used by relevance/comments).
Missions: **`missions`** (permanent: `stance`, `status` active|paused, `agent_mode`,
`dynamic_count`, `tactic` — default `dynamic` = per-post tactic from thread mood vs stance),
**`mission_targets`** (kind channel|post, status active|suggested|rejected,
source operator|agent), **`mission_squads`** (roster, caste role).
Knowledge: **`knowledge_facts`** (pgvector RAG), `scraping_landscape` (sources),
`captured_raw_events`, `scouted_targets`, `social_post_targets`, `campaigns`.
Activity: **`agent_activity_logs`** (durable: comment|reply|react), **`decision_events`**
(durable WHY the swarm did/didn't act: kind relevance|skip|comment, detail = recognized text/
reason, verdict), `account_audit_logs`, `virtual_devices`.

## Redis keys

Queues: `queue:raw_events` (HUGINN→ORPHEUS autonomous), `queue:execution_tasks`
(ORPHEUS/engines→MYRMIDON), `queue:mission_gen` (MYRMIDON↔ORPHEUS request/reply, with
`reply:missiongen:<id>` and `reply:relevance:<id>`).
Telemetry: **`stream:agent_events`** (capped stream the Live Ops feed tails).
Dialogue: `morpheus:dialogue:watches` (hash), `morpheus:dialogue:handled`.
Targets: `morpheus:target:lastseen` (hash, also `mission:<id>:<channel>`),
`morpheus:target:rate:*` (hourly caps), `morpheus:suggest:checked:<mid>:<ident>` (6h
re-scan marker for agent target suggestions).
Anti-repeat: `morpheus:recent_outputs:<agent>` (capped list of the agent's last comments).
Profiling: `morpheus:profile:heavy:<platform>:<ref>` (24h gate), `…:themes:…` (4h gate).
Reliability/locks: `morpheus:tg_lock:<agent>` (session lock), `morpheus:tg_cooldown:<agent>`,
`morpheus:amplified:<url>` (once-per-post amplification). Metrics: `metrics:*`.

---

## How the swarm actually works (pipelines)

1. **News → knowledge (RAG):** each agent's `role=news` channels → MYRMIDON `target_engine`
   reads new posts → DAEDALUS `/knowledge/internal/ingest` → LLM classify + `nomic-embed-text`
   + pgvector dedup → `knowledge_facts`. ORPHEUS grounds comments on these (`rag.fetch_fresh_context`).
2. **Mission-driven commenting (primary):** active mission → roster **alpha** scans the
   mission's `active` channel targets (now incl. **media-only posts**; caption-less photo/voice
   posts are "read" first — `media_reader`: VLM+OCR / HEIMDALL STT) → ORPHEUS LLM-relevance vs
   the mission's goal+stance (penalties OFF for the YES/NO call) → seeds an execution task
   (goal+stance+tactic+mission_id+media_context) → MYRMIDON: ORPHEUS picks a **dynamic per-post
   tactic** (post+thread mood vs stance → `amplify`|`soft_support`|`aggressive_displacement`|
   `sentiment_shift`), then writes the comment (persona + RAG + memory + thread mood + **media
   context** + stance, anti-echo, anti-repeat, regen) → posts it → registers a dialogue watch →
   **swarm amplification**: mission-roster **beta** drops a cheap lite comment (inheriting the
   alpha's tactic), **gamma** an emoji reaction.
   *Also:* roster bots propose new mission targets from their own channels
   (`_suggest_targets_for_mission` → `/missions/internal/suggest-target`).
3. **Conversations:** a watch on the bot's comment is polled; a real human reply → ORPHEUS
   reply-mode → MYRMIDON answers (and watches its own answer → multi-turn).
4. **Memory:** every comment/reply summary is saved to MUNINN per agent↔opponent; recalled next time.
5. **Castes:** alpha = full cognitive (smart, human-like; picks the per-post tactic). beta =
   cheap "lite" support (no RAG/memory/thread, short; inherits the alpha's tactic). gamma =
   emoji reaction only (no LLM).
6. **Reliability:** short FloodWait → wait+retry; long → cooldown; PeerFlood → 1h cooldown;
   fatal session errors → account `banned` (+ profile suspended), dropped from the active pool.
7. **Active hours:** posting (seed/amplify/reply) only inside the persona's
   `active_hours_start`..`_end` window (`schedule.in_active_hours`, swarm tz); read-only news/
   profiling run 24/7. So the swarm has a human daily rhythm, not 24/7 chatter.

---

## Working rules (follow these)

- **On "коммит" / "commit":** UPDATE `README.md`, `CLAUDE.md` and `walkthrough.md` to reflect
  this batch of changes, THEN `git add -A` and commit. (The operator asked that these three
  docs always be refreshed at commit time. Keep them accurate.)
- Commit **only when asked**. Stage with `git add -A`; the branch is a WIP feature branch.
- Never commit secrets. **`.env` is gitignored** (holds `TG_API_ID`/`TG_API_HASH`,
  `SUPERADMIN_PASSWORD`, DB creds). Never stage `*.png` screenshots, `.playwright-mcp/`,
  `*.session`. Clean temp diagnostic scripts after use.
- **Verify on real data.** Operator credentials: user `morpheus`, password from
  `.env` `SUPERADMIN_PASSWORD` (default `CHANGE_ME_IMMEDIATELY`). For UI checks, inject the
  JWT into `localStorage.daedalus_token` via Playwright and navigate (the login form is awkward
  to automate). Resize the browser to ~1440px for representative screenshots.
- Three **real TG accounts** exist: `clone_alpha_91eea738` (alpha), `clone_alpha_bd35bcad`
  (beta), `clone_alpha_0e795b8d` (gamma). Test channel: `@tashkent_news333`. Be considerate —
  every test comment posts to a real channel.
- ORPHEUS/MYRMIDON are **Redis workers/daemon threads**, not HTTP for generation. After
  restarting them, the profile cache/loops take ~30s to warm; wait before asserting failure.
- The autonomous engines run live once deployed: they will post to real channels (throttled
  ≤1/channel/hr, ≤4/agent/hr). Pause an agent or a mission to stop it.
- Keep operator UI in **Russian** (matches the rest); backend logs/comments in English.

## Known gotchas (hard-won)

- One Pyrogram session per account, shared by missions/dialogue/news/reactions → a per-agent
  **session lock** serializes them. Use a **fresh event loop per call** (`_run`) or you get
  "got Future attached to a different loop" in daemon threads.
- Pyrogram resolves large `-100…` chat ids unreliably cold → prefer `@username`; reply via
  `message.reply_text()`; warm the discussion peer via `get_discussion_message`.
- Discussion replies often have `from_user=None` (privacy/anonymous) — still real humans;
  answer them, scope memory by `anon:<chat>`/`thread:…`.
- `qwen2.5:3b` parrots the input → `guardrails.is_echo` + regen; rehashes its own past
  comments → `guardrails.is_repeat` + `morpheus:recent_outputs`. A bigger `TEXT_MODEL_NAME`
  would help; the guards/prompts are model-agnostic.
- **Anti-parroting penalties corrupt SHORT classification.** `repeat_penalty`/`frequency_penalty`
  (good for comments) push the model OFF the clean `да`/`нет` token on YES/NO relevance/tactic
  calls → garbage like `'дятьнет'`. Always pass `penalties=False` (+ low temp) for classification.
- **Moondream (VLM) can't read text in images** — it hallucinates a scene (e.g. "a book cover")
  on a Telegram "text card". Read text-bearing images with **Tesseract OCR** (MYRMIDON
  `media_reader`); the VLM is only for real photos. Also keep VLM prompts SHORT (it returns
  empty on long/structured prompts).
- Relevance judges a post **in a vacuum** today → a vague but on-topic phrase (`«опять эти
  машины»` on a Tashkent traffic channel) is missed. The fix is **Channel Profiling**
  (`CHANNEL_PROFILING.md`, designed, build next): judge in the channel's topic/geo/news context.
- `app/main.py` registers signal handlers at import — guarded to main-thread only (daemon
  threads re-import it).
- Mission DAG reconciler is legacy; it only touches `running`/`amplifying` so new
  `active`/`paused` missions are safe.
