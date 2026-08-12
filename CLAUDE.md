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
  `/dialogues`. `router_knowledge.py` — RAG facts ingest/search/list, the boilerplate scrubber +
  junk gate, dedup/merge, `/facts/cleanup` and `/facts/refetch` (backfill) + `/knowledge/internal/
  by-geo` (recent facts by PLACE = **`geo_tags`** overlap, canonicalised both sides via `geo.py`).
  `geo.py` — canonical place vocabulary (ru/en/uz, both Uzbek scripts) + `places_in_text` (a place
  the source never named is a hallucination, not evidence). `refetch.py` — re-reads the article
  behind facts stored as feed teasers. `landscape.py` — scraping sources + `/internal/report`
  (per-pass source health: ok|degraded|dead). `router_scouting.py`, `router_auth_factory.py` (TG login/code),
  `router_factory.py` (clone factory), `router_sandbox.py`, `db_explorer.py`,
  `classifier.py`/`embeddings.py` (LLM classify + embed for knowledge), `genesis_engine.py`.
  **`router_simulation.py` + `models_simulation.py` + `sim_generator.py` + `sim_landscape.py`
  — the SIMULATION polygon** (isolated test environment; see `SIMULATION.md`). Its
  `/simulation/import/telegram` pulls real posts WITH the real comments under them by
  delegating the MTProto read to MYRMIDON (`GET :8003/api/v1/telegram/{agent}/export`,
  read-only) — the public `t.me/s/` preview shows no discussion, and a thread populated
  only by our own agents cannot exercise the crowd-reading half of the pipeline. In the UI it is
  the «Импорт тредов» tab of the polygon's Landscape modal (`simulation/ToolModals.tsx`).
  `channel_profiler.py` (LLM strict-JSON per-channel profile + hot themes) +
  `router_channels.py` (internal `/channels/internal/{profile,themes}` build + `…/profile`
  GET; operator `GET /channels/profiles` for the UI). `router_decisions.py` (internal
  `/decisions/internal/log`; operator `GET /decisions` — the durable decision history).
- React SPA (`daedalus/frontend/src/`): **fully migrated to Mantine 7** (Stages 65–77 — the redesign
  is DONE). `main.tsx` wraps the app in `MantineProvider` (forced dark theme). `App.tsx` = Mantine
  **`AppShell`** (fixed navbar + `ScrollArea` nav + single scrolling `Main`) + a data-driven `NAV`
  array + **hash routing with entity ids**: `useHashRoute` parses `#/<view>/<id>` so `#/souls/<id>`
  opens that entity's **full-screen** detail (refresh/deep-link/back-forward all work).
  - **Reusable primitives in `src/ui/`** (use these for any new screen): `DataView` (Mantine `Table`:
    search, sortable cols, pagination, sticky header, **horizontal scroll** via `Table.ScrollContainer`,
    row→detail), `DetailPage` (full-screen master-detail scaffold: back + header/actions + body +
    sticky save footer), `EntityPicker` (searchable/sortable **pick-from-list** modal — used everywhere
    instead of typing IDs), `StatTile` (KPI tile + inline SVG sparkline).
  - **One `*Screen.tsx` per view** (`src/components/`): `Dashboard` ("Центр управления" — KPI tiles +
    `SystemDiagnostics` tab), `LiveOps` ("Командный центр" live feed), `SwarmScreen` ("Рой" KPI hub +
    drill-downs), `AccountsScreen`, `SoulsScreen` (5-tab full-screen persona editor), `SoulGenesisView`,
    `CloneFactory`, `AuthFactory` (Stepper login wizard), `LandscapeScreen`, `NewsHubScreen`,
    `KnowledgeScreen`, `ChannelProfilesScreen`, `ScoutingScreen`, `MissionsScreen`, `DeviceGrid`,
    `SandboxConsole`, `DecisionsScreen`, `ActivityScreen`, `DatabaseExplorer`, `Login`, `ChannelManager`
    (full-screen channel editor opened from Souls/Accounts), and **`simulation/`** — the polygon's
    own 3-column workspace (`SimulationScreen` + `ActivityFeed`/`ChannelFeed`/`ThreadView`/`RightPanel`
    + modals); it deliberately uses modals, not full-screen pages, because it is a Telegram-like
    workspace, not a CRUD screen. See `SIMULATION.md`. Editing a selected item is always a
    **full-screen page**, never a modal/drawer; cross-links jump account↔soul↔mission↔channel↔decisions
    via a global `goTo(view,id)`. **No per-component CSS** — only `App.css` (theme vars/base); style
    with Mantine props.
  - Operator-facing UI strings are **in Russian**. `db_explorer` validates table reads against LIVE
    tables. **`DAEDALUS_CAPABILITIES.md` is the full screen↔endpoint↔data map** (read it for the UI).
  - **The old pre-Mantine components are deleted** (`DataTable`, `SidePanel`, `SoulsContext`,
    `AccountsManager`, `MissionDeck`, `NewsHubInspector`, `MuninnExplorer`, `ChannelProfiles`,
    `LandscapeManager`, `DecisionLog`, `ActivityStream`, `ScoutingRadar`, `SwarmDashboard`) — don't
    resurrect them. **The UI is considered complete; current focus is the functional/swarm side
    (ORPHEUS/MYRMIDON/pipelines below).**

### ORPHEUS (`orpheus/app/`) — cognitive core (NO HTTP for generation)
- `main.py` — Redis worker, multi-key `BRPOP` on `queue:raw_events` + `queue:mission_gen`
  + `queue:sim_gen` (the isolated simulation polygon → `simulation.py`, which persists
  nothing: no MUNINN memory, no recent-output history, no metrics).
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
  caption-less media posts are "read" first so relevance is grounded in the photo/audio). Stage 38
  reads a bounded live thread *before* relevance, ranks all candidates (strong verdict → discussion
  size → freshness), proactively checks target health and skips freshly blocked targets; then it
  seeds a comment (and `swarm.py` amplifies). Emits `media_read`/`relevance`/`rate_skip` to Live Ops.
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
  DAEDALUS `/knowledge/internal/ingest` (returns False when rejected as boilerplate),
  mirrors into the News Hub (`capture_event`, display only — never the execution queue) and
  reports each pass (`report_scrape`).
- **`article_fetcher.py`** — shared article extraction (trafilatura). Both scrapers open the
  ARTICLE, not the link: a feed entry is an announcement carrying 4–44× less text than the page
  (BBC ×23, gazeta.uz ×18, podrobno ×44 — RT ×0.9 is the exception). `better_text` keeps whichever
  version is richer per item, so RT is not silently downgraded.
- `scrapers/rss_scraper.py` (was `test_rss.py` at the repo root) — feeds, dedup, article fetch,
  publication date. `RSS_MAX_ARTICLE_FETCH` is **both** the entry slice and the fetch budget; they
  must stay equal (when they diverged, entries past the budget were stored as stubs *and* stamped
  with the 24h dedup key, so they could never be retried).
- `scrapers/web_scraper.py` — front page → dated article links → body. Requires a date segment in
  the path: a hyphenated-slug heuristic was measured too weak (CNN names its section hubs the same
  way). Reports an error when it opens articles but extracts none, so a site like `daryo.uz` —
  which does not expose article bodies in its HTML at all — shows as `degraded` instead of silent.

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
Missions: **`missions`** (permanent: `stance`, explicit position `our_side` / `opponent` /
`key_points` / `red_lines`, `status` active|paused, `agent_mode`, `dynamic_count`, `tactic` —
default `dynamic` = per-post tactic from thread mood vs our side),
**`mission_targets`** (kind channel|post, status active|suggested|rejected, target health
`unknown`|`ok`|`blocked`|`degraded`,
source operator|agent), **`mission_squads`** (roster, caste role).
Knowledge: **`knowledge_facts`** (pgvector RAG; `geo_tags` = canonical PLACES only, `variants` =
wordings a merge superseded, `published_at` = the SOURCE's date — freshness must not mean "scraped
today"), `scraping_landscape` (sources + health: `health`, `health_reason`, `last_item_count`,
`consecutive_empty`, `last_scraped_at`), `captured_raw_events` (News Hub, display only),
`scouted_targets`, `social_post_targets`, `campaigns`.
Activity: **`agent_activity_logs`** (durable: comment|reply|react), **`decision_events`**
(durable WHY the swarm did/didn't act: kind relevance|skip|comment, detail = recognized text/
reason, verdict), `account_audit_logs`, `virtual_devices`.
Simulation (**isolated polygon, never production**): `sim_worlds`, `sim_channels`, `sim_posts`,
`sim_post_revisions`, `sim_comments`, `sim_accounts`, `sim_personas`, `sim_missions`,
`sim_mission_agents`, `sim_knowledge`, `sim_landscape_sources`, `sim_jobs`, `sim_events` —
own `SimBase`, no FK into production. See **`SIMULATION.md`**.

## Redis keys

Queues: `queue:raw_events` (HUGINN→ORPHEUS autonomous), `queue:execution_tasks`
(ORPHEUS/engines→MYRMIDON), `queue:mission_gen` (MYRMIDON↔ORPHEUS request/reply, with
`reply:missiongen:<id>` and `reply:relevance:<id>`), **`queue:sim_gen`** (DAEDALUS↔ORPHEUS,
SIMULATION polygon only, with `reply:simgen:<id>` — deliberately NOT the mission queue and
never `queue:execution_tasks`, so a polygon run cannot reach a real channel).
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
   reads new posts; HUGINN's RSS/web scrapers open the **full article** (`article_fetcher`) →
   DAEDALUS `/knowledge/internal/ingest` → scrub boilerplate + reject junk → LLM classify
   (layers/categories/tags/**places**) + `nomic-embed-text` + HNSW dedup (high cosine **and**
   shared vocabulary; non-destructive) → `knowledge_facts`. ORPHEUS grounds comments on these
   (`rag.fetch_fresh_context`), filtered by age (`published_at`, 14d) and admitted lexically.
2. **Mission-driven commenting (primary):** active mission → roster **alpha** scans the
   mission's `active` channel targets (now incl. **media-only posts**; caption-less photo/voice
   posts are "read" first — `media_reader`: VLM+OCR / HEIMDALL STT) → ORPHEUS LLM-relevance vs
   the mission's goal+stance (penalties OFF for the YES/NO call) → seeds an execution task
   (goal+stance+tactic+mission_id+media_context) → MYRMIDON: ORPHEUS picks a **dynamic per-post
   tactic** (post+thread mood vs stance → `amplify`|`soft_support`|`aggressive_displacement`|
   `sentiment_shift`), then writes the comment (persona + RAG + memory + thread mood + **media
   context** + explicit mission position, anti-echo, anti-repeat, regen) → posts it → registers a dialogue watch →
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
- Relevance/tactic prompts need a **concrete anchor, not an abstract stance salad.** A mission
  `stance` written as a tag list (`«Суверенитет, Социализm, Технократия…»`) makes qwen2.5:3b
  default to НЕТ on relevance and write muddled (even wrong-direction) rebuttals. The relevance
  gate now anchors on the goal + extracted **mission entities** (`orpheus._mission_entities`)
  with a plain "связано ли с темой?" (+ a keyword **recall-override** `_entity_hit`); comments
  read the concrete `stance` as "the side you argue from". **Operator rule: write `stance` as a
  short argued claim** (the model can only argue a position it can read as a position).
- **Mission target identifiers must be canonical.** A raw `https://t.me/foo` URL is unresolvable
  by Pyrogram → the channel is silently dropped and the mission looks idle. `router_missions.
  canonical_identifier` normalises on write; `target_engine._resolvable_ident` defensively on
  read; `tg_client.fetch_new_posts` surfaces an unresolvable ref (`channel_unresolved` event)
  instead of swallowing it. The same channel can be a target of several missions — the Live Ops
  relevance verdict is prefixed with the **mission title** so they're not confused.
- **Tactic heat = real insults, NOT exclamation marks.** `persona.tactic_from_mood` once treated
  `"!!"`/`"!?"` as a flame and downgraded a direct rebuttal to the soft `sentiment_shift`; an
  emphatic-but-civil opposing post is a normal disagreement → `aggressive_displacement` (rebut
  the author directly). Only `_HEAT_MARKERS` (slurs/insults) pick the cunning reframe.
- **Channel context is for TONE/audience/language, not the comment's topic.** The `[Контекст
  канала]` block (`assemble_mission_prompt`) used to say "write like a local about these topics"
  → comments drifted to the channel's everyday themes (пробки/инфраструктура) on an off-theme
  (e.g. geopolitics) post. It now explicitly grounds tone only; the post + mission drive content.
- **qwen2.5:3b tacks on garbage hashtags / nonce tokens** (`#УЗБЕКИСТАНишегизилуенет`).
  `guardrails.clean_output` strips them (sanitise, not reject) before posting. Bigger model = fewer.
- Relevance judges a post **in a vacuum** by default → a vague but on-topic phrase (`«опять эти
  машины»` on a Tashkent traffic channel) is missed. **Channel Profiling** (built) feeds the
  channel's topic/geo/news context into the gate so a post is judged IN context, not blind.
- **Ask the gate whether we can JOIN, not whether the post is ON our subject** (Stage 38). The old
  question («связано ли сообщение с темой миссии?») is honestly answered «нет» for almost every post
  on a general-news channel — measured: `llm='нет'` in **50/50** live calls, every positive verdict
  came from the crude keyword override. Reframed to «может ли наш человек естественно вступить в это
  обсуждение со своей позицией?» with a graded ДА/СЛАБО/НЕТ, the same model on the same 14 posts
  accepts 11 (all by its own judgement, 3/3 stable). See `DIAGNOSIS.md`.
- **Clean the post before judging it.** OCR dumps of TV schedules/posters («25 ИЮЛЯ 13:55
  КОММЕНТАТОРЫ: …»), promo tails («Наш канал в MAX») and link soup make a 3B model answer «нет» to
  everything. `orpheus/app/textutil.py` strips them and drops schedule dumps outright.
- **`nomic-embed-text` does NOT separate topics on this corpus.** A traffic-jam query scores
  «Град уничтожил бойцов ВСУ» at 0.74 and a genuinely relevant fact at 0.85 — no absolute threshold
  splits that (the old floor of 0.5 admitted everything, which is why "RAG didn't seem to affect the
  answers"). Similarity now only fetches candidates; admission is **lexical** (shared mission/post
  vocabulary), similarity breaks ties. Empty context is honest — never pad the prompt with noise.
  Also: nomic's `search_query:`/`search_document:` prefixes made separation *worse* here — measured,
  don't add them. The same blindness broke **dedup**: unrelated same-language stories reach 0.849
  (0.90 on the long texts that actually arrive) while true duplicates sit at 0.917–0.935, so the old
  0.85 merge floor ate real news — one fact was 16 different posts, 37% of all ingested bodies were
  discarded. A merge needs cosine **and** shared vocabulary, and keeps the loser in `variants`.
- **The pgvector index was returning the WRONG neighbour.** `ivfflat(lists=100)` over ~800 rows puts
  ~8 rows per list and `ivfflat.probes` defaults to 1 — a search examined ~1% of the table. Measured:
  indexed top-1 matched the true top-1 in **3/14** probes; a 0.968 duplicate came back as a 0.845
  stranger. Now **HNSW** (recall 30/30). If you ever reintroduce IVFFlat, size `lists` to the row
  count and raise `probes`.
- **An RSS entry is an announcement, not the news.** The article page carries 4–44× the feed's
  `summary` (BBC ×23, gazeta.uz ×18, podrobno ×44). Store the feed text and the swarm "knows" only
  headlines — which is why Telegram-sourced facts always read better. **RT is the exception** (its
  pages extract to *less* than its feed), so `better_text` compares per item instead of assuming.
- **Some sites cannot be scraped at all.** `daryo.uz` never puts article text in its HTML: both
  trafilatura modes return the comment widget and a subscription ad. Reject rather than store —
  and report it, or a source that finds links but extracts nothing looks perfectly healthy.
- **Geo must be canonical AND verified.** Tags arrive in the source's language (`ташкент` matched 0
  facts, `uzbekistan` 24), so places are canonicalised through `daedalus/app/geo.py`. And qwen2.5:3b
  *invents* geography — a Zaporizhzhia blackout came back tagged `узбекистан`+`ташкент`, which alone
  is enough to serve a Ukraine story to a Tashkent channel. `places_in_text` keeps only places the
  text actually names (stemmed: «россия» must find «России»).
- **A merge must not freeze the poorest telling.** The same story arrives twice — feed teaser first,
  full article later. First-seen-wins would keep the stub forever, so a materially richer incoming
  text replaces the content and re-embeds.
- **Boilerplate is not cosmetic.** 497/497 RT facts ended in "Читать далее" and 44/90 daryo facts
  were *nothing but* chrome — all embedded and offered to the bots as world knowledge. Scrub at
  ingest (one gate in `router_knowledge`, covering every scraper) and truncate on a sentence
  boundary, or facts end mid-word.
- **`403 CHAT_GUEST_SEND_FORBIDDEN` is a generic RPCError in Pyrogram**, not `ChatWriteForbidden` —
  so the join-and-retry branch never fired and every mission comment on such a channel died (3/3 on
  `@Match_TV`). Handle it explicitly: join the discussion group, retry once.
- **"Post has comments disabled" is a POST-level fact, not a target-level one.** Blocking the whole
  channel on it kills a working target (`myrmidon/app/target_health.py` separates the two scopes).
- **A mission needs an explicit side.** With only free-text `narrative_goal` + `stance` the model
  guesses whose side it is on — and a contradiction between them (goal «Аргентина должна была
  выиграть» vs stance «Аргентина проиграла из-за тренера») yields comments arguing against the
  mission's own goal. Fill `our_side` / `opponent` / `key_points` / `red_lines`.
- `app/main.py` registers signal handlers at import — guarded to main-thread only (daemon
  threads re-import it).
- Mission DAG reconciler is legacy; it only touches `running`/`amplifying` so new
  `active`/`paused` missions are safe.
