# CLAUDE.md — MORPHEUS project guide

This file orients a fresh session. Read it fully before working. Companion docs:
**`README.md`** (what the system is, how to run it) and **`walkthrough.md`** (work
log, current state, next steps). Keep all three current — see "Working rules".

**Current plan of record: `ROADMAP.md`** — the prioritised fix plan drawn from three audits
(`SYSTEM_STATE.md` — end-to-end testing, `FUNCTIONAL_GAPS.md` — what the swarm cannot do,
`CODE_AUDIT.md` — code quality with measurements). Model replacement and hardware upgrades are
**postponed by the operator**; UX/UI work comes only after the roadmap is done.

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
| **searxng** | searxng/searxng | (8080 internal) | **Search** — how the swarm finds what it does not know (Stage 47) |

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
  the **«Импорт поста из Telegram»** button in the polygon's right column
  (`simulation/RightPanel.tsx` → opens `ToolModals.tsx` on its import tab).
  **`tools.py` — how the swarm finds out what it does not know (Stage 47)**: `search()` over the
  compose-local **SearXNG**, and `lookup()` = search → read the pages behind the top results
  (reusing `refetch.fetch_article_text`) → file them through the ORDINARY knowledge pipeline →
  return the findings. Exposed as `POST /knowledge/internal/lookup`. Everything read is filed on
  purpose: a search result used once and discarded leaves the corpus as poor as before. The model
  is never asked to emit tool-call JSON (it cannot) — the DECISION to search is a one-word
  classification, the query is built from extracted terms, the tool itself is code.
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
  **Stage 46 — the objection, and a technique against it**: `_extract_objection` QUOTES whoever
  argues with us (asking the model to JUDGE whether anyone objects gets «НЕТ» — measured 2/2 on a
  thread full of opposition), `_grounded_objection` throws the quote away unless the thread really
  contains it, and `_technique_for` picks one word out of `factual_correction` / `reframe` /
  `concede_and_redirect` / `ask_evidence` — with `avoid` so the teammate answering the same
  objection cannot repeat the opener's move. Asked only when the mood reading is not AGREE.
  **Stage 47 — going and finding out**: `_needs_fresh_data` (a free keyword pre-filter, then one
  word from the model) → `_lookup_query` (entities + the channel's place) → `_fetch_fresh` (calls
  DAEDALUS `lookup`, cached per query in Redis) → a `[Свежие данные]` prompt block. A failed
  search yields no block at all: the bot answers from what it knows and says nothing about what it
  does not. `_crowd_thread` — mood and objection are read over the CROWD's messages, never over a
  thread that already contains our own.
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
  the agent's OWN recent comments) + **`_script_bleed`** (a run of CJK/kana/hangul/Arabic/Hebrew/
  Devanagari/Thai INSIDE a Cyrillic or Latin comment — the model leaking training data
  mid-sentence, which every other check passed; a comment written wholly in such a script is fine). `coordination.py` — legacy DAG beta amplification (the live
  swarm amplification is now in MYRMIDON `swarm.py`). `media_enricher.py` — VLM (Ollama) + STT
  **delegated to HEIMDALL** (no local Whisper). `telemetry.py` — `emit()` → `stream:agent_events`.

### MYRMIDON (`myrmidon/app/`) — execution swarm (Pyrogram)
- `main.py` — consumes `queue:execution_tasks`; `_execute_telegram` (comment via
  `text_provider`→ORPHEUS, or `action_type=react`); starts `dialogue_engine`,
  `target_engine`; respects account cooldown. **Stage 46 — waiting is a property of the TASK**:
  `unpublishable_reason` drops what can never publish, `_due_or_defer` parks a not-yet-due task in
  the `morpheus:exec:scheduled` ZSET and `start_task_scheduler` hands it back when it comes due, so
  one agent's pacing delay no longer stops every other agent. `_dossier_file` writes the mission's
  case file (`opponent` on extraction, `counter` after the answer, `said` after publication — the
  last one used to be filed from `text_to_publish`, which is empty by design on cognitive tasks, so
  the "one memory for the roster" recorded nothing at all).
- `drivers/tg_client.py` — `TelegramDriver` (all TG ops): `_read_thread` returns the discussion
  TWICE — the whole thread for the writer (so it does not repeat our own people) and the crowd
  alone for the judge (mood, and which objection to answer), because a thread the mission has
  already worked reads as agreeing with us. `is_self` is not enough to tell ours apart — it marks
  only the reading session's messages — so it uses `outcome_engine.swarm_identities`.
  `execute_comment` (channel comment in the linked discussion group; reads post text + thread
  mood + **media context**),
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
- `swarm.py` — the rest of the roster joins the discussion the **opener** entered:
  `support` answers the objection with a DIFFERENT technique (full generation — "answer the
  specific objection, but cheaply" just reproduces the ally), `closer` speaks only into a thread
  that turned hostile (`thread_is_hostile`: OPPOSE + real insults, never mere emphasis), `scout`
  does not amplify, and a `gamma` caste still drops an emoji reaction. A roster still carrying the
  legacy castes keeps the old behaviour exactly.
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
Missions: **`missions`** (permanent. **`phase`** draft|recon|ready|active is the lifecycle —
a mission may NOT go active while its dossier holds no fact, because the old on/off `status` let it
start arguing before anyone established what was true; `status` remains the switch the engines read.
**`our_side` is THE claim the team asserts**, `narrative_goal` is what the AUDIENCE should end up
thinking, and **`stance` is LEGACY** — the three used to express the same thing with no rule about
which wins, which is how «За аргентину» published a comment agreeing with the defeat it existed to
dispute. Plus `opponent` / `key_points` / `red_lines`, `agent_mode`, `dynamic_count`, `tactic` —
default `dynamic` = per-post tactic from thread mood vs our side),
**`mission_targets`** (kind channel|post, status active|suggested|rejected, target health
`unknown`|`ok`|`blocked`|`degraded`,
source operator|agent), **`mission_squads`** (roster; `assigned_role` = **scout | opener | support |
closer** — what the member DOES. alpha/beta/gamma described how expensive the generation was, so the
"team" was one bot speaking and two repeating it more cheaply; `caste` keeps that cost axis).
**`mission_dossier`** (the team's shared case file — see below).
**`mission_outcomes`** (did the tone move, did anyone engage — see below).
Knowledge: **`knowledge_facts`** (pgvector RAG; `geo_tags` = canonical PLACES only, `variants` =
wordings a merge superseded, `published_at` = the SOURCE's date — freshness must not mean "scraped
today"), `scraping_landscape` (sources + health: `health`, `health_reason`, `last_item_count`,
`consecutive_empty`, `last_scraped_at`), `captured_raw_events` (News Hub, display only),
`scouted_targets`, `social_post_targets`, `campaigns`.
Mission recon: **`mission_recon.py`** (`POST /missions/{id}/recon`) builds the mission's
factual base into the dossier before it speaks. Retrieval is **lexical-first**, unlike
per-comment RAG, and measured: asked for the transport mission, the top-40 by cosine were
Novorossiysk transport, Trump/Ukraine, weightlifters and a fire, so an embedding-first
recon reports "we know nothing" falsely. Terms are weighted by **IDF over the scanned
slice**, because a plain share-of-words test admits articles reusing the mission's filler
(«развитие», «город»). When nothing is filed it returns `missing_terms` — the mission's own
words the corpus never mentions — which is the actionable half: on mission #10 «пробки»,
«полоса» and «решают» appeared in 0 of 1252 facts, i.e. the swarm was about to argue from
an empty base.
Mission state: **`mission_dossier`** (the team's shared case file — `kind` =
`fact` (with source) | `opponent` (what the other side argues here) | `counter` |
`said` (an argument our side already used, scoped to a post_url). Anti-repeat used to be
per-AGENT (`morpheus:recent_outputs:<agent>`), so alpha/beta/gamma could each replay the
same argument in one thread; the dossier is one memory for the whole roster and is fed
into the prompt as three blocks — established facts, the opponent's lines, and what we
already said HERE).
Activity: **`agent_activity_logs`** (durable: comment|reply|react; `mission_id` = which
mission caused it — added Stage 42, before which 46 published comments belonged to nobody
and no mission could be measured), **`mission_outcomes`** (one row per mission×discussion:
`mood_before`/`mood_after` = the crowd's stance toward us before and after we spoke,
`thread_grew` separating "tone did not move" from "nobody said anything", `our_comments`,
`human_replies` — the operator's success measure is tone change + people drawn into
dialogue), **`decision_events`**
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
Pacing: **`morpheus:exec:scheduled`** (ZSET of tasks waiting out their delay, scored by the unix
time they come due — the delay is no longer slept in the consumer). Search:
`morpheus:lookup:<hash>` (a lookup's findings, 30 min, so a roster answering one post does not
pay for the same search three times).

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
   tactic**. Stage 46: when the crowd is not already with us, ORPHEUS first QUOTES the strongest
   line arguing against our side (verified present in the thread) and picks a **technique against
   that objection** — `factual_correction` (only when the dossier holds facts) | `reframe` |
   `concede_and_redirect` | `ask_evidence`; with no objection it falls back to the mood-derived
   `amplify`|`soft_support`|`aggressive_displacement`|`sentiment_shift`. Stage 47: if answering
   depends on something that CHANGES (score, price, today's news) it searches the web first and
   weaves in what it read. Then it writes the comment (persona + RAG + memory + crowd mood +
   **media context** + explicit mission position + the objection, anti-echo, anti-repeat, regen) →
   posts it → registers a dialogue watch → **the team joins**: `support` answers the same objection
   with a different technique, `closer` cools a hostile thread, a `gamma` caste reacts.
   *Also:* roster bots propose new mission targets from their own channels
   (`_suggest_targets_for_mission` → `/missions/internal/suggest-target`).
3. **Conversations:** a watch on the bot's comment is polled; a real human reply → ORPHEUS
   reply-mode → MYRMIDON answers (and watches its own answer → multi-turn).
4. **Memory:** every comment/reply summary is saved to MUNINN per agent↔opponent; recalled next time.
5. **Roles vs castes — two different axes (Stage 45).** A **role** is the JOB in the discussion:
   `scout` (establish what is claimed, take no side), `opener` (first substantive argument),
   `support` (answer the objection actually raised), `closer` (de-escalate). A **caste** is the COST
   tier: alpha = full cognitive, beta = cheap "lite" (no RAG/memory/thread, inherits the tactic),
   gamma = emoji reaction only. They were conflated, so the "team" was one bot speaking and two
   repeating it more cheaply — an echo, not a division of labour. Legacy castes still resolve as
   roles so old rosters keep working.
5a. **Mission phases.** draft → recon → ready → active. `POST /missions/{id}/phase` REFUSES `active`
   while the dossier holds no fact: recon on mission #10 found its own key terms in 0 of 1252 facts,
   i.e. the swarm was about to argue about transport with no fact about transport. **The engines
   read `phase`** (Stage 46) — while they read the legacy `status`, mission #10 sat in `recon` with
   an empty case file and went on commenting in a real channel, because the gate only existed at
   the moment of the transition. `status` is kept in step as the legacy mirror, and pausing returns
   a mission to `ready` only if it still has facts (otherwise `draft`, or the label promises
   something the gate will refuse).
5aa. **Reconnaissance matches the SUBJECT, and searches when the base is empty** (Stage 47). A
   mission's text is mostly its own argument, which no article repeats, so matching on all of it
   asked the corpus for an article that argues our case. One short generation names the subject
   («дороги, автобусы, метро»), the mission's place comes from its targets' channel profiles, and a
   fact is admitted only if the subject is what it is ABOUT (named ≥3 times, or ≥2 distinct subject
   words) AND it is about our place. Filing less than three facts triggers a web search for the
   subject, after which the base is re-read. Measured end to end: mission #10 went from 0 facts to
   6 relevant ones (4 from the corpus, 6 found online, junk rejected).
5b. **The dossier is one memory for the roster.** Anti-repeat lived in
   `morpheus:recent_outputs:<agent>` — per AGENT — so alpha, beta and gamma could each play the same
   card in one thread. `mission_dossier` (fact | opponent | counter | said, `said` scoped to the post)
   is filed on publication and fed back as three prompt blocks.
5c. **Outcome = did the tone move and did anyone engage.** Measured over the replies **AFTER** our
   first comment, never over the whole thread: one comment — and a coordinated three — left a
   21-comment thread's aggregate verdict unchanged every time, so a whole-thread reading is
   structurally blind and would report "no effect" for any implementation. `outcome_engine` re-reads
   read-only after `MISSION_OUTCOME_AFTER_HOURS`; nobody speaking after us yields a NULL verdict, not
   "unchanged".
6. **Reliability:** short FloodWait → wait+retry; long → cooldown; PeerFlood → 1h cooldown;
   fatal session errors → account `banned` (+ profile suspended), dropped from the active pool.
7. **Outcome measurement (Stage 42):** when a mission first speaks in a discussion,
   MYRMIDON opens a `mission_outcomes` row with the crowd's stance toward us
   (`AGREE|NEUTRAL|OPPOSE`, the verdict ORPHEUS already computes to pick the tactic and
   used to discard). `outcome_engine` returns after `MISSION_OUTCOME_AFTER_HOURS` (6),
   re-reads the same thread read-only, asks ORPHEUS the SAME question (`mode=mood`) and
   records the delta plus how many real people answered OUR comments. A thread that
   cannot be read is closed as `unreadable` with a NULL verdict — "we don't know" is
   honest, "no change" would be invented. `thread_grew` keeps "the tone did not move"
   distinct from "nobody said anything".
8. **Active hours:** posting (seed/amplify/reply) only inside the persona's
   `active_hours_start`..`_end` window (`schedule.in_active_hours`, swarm tz); read-only news/
   profiling run 24/7. So the swarm has a human daily rhythm, not 24/7 chatter.

---

## Code rules (audited 14 Aug 2026 — see `CODE_AUDIT.md` for the measurements)

Two commands enforce these; run both before asking for review:

```bash
python3 tools/check_architecture.py                     # cycles, lazy imports, main-as-library
ruff check daedalus/app orpheus/app myrmidon/app huginn/app   # configured in pyproject.toml
```

`tools/check_architecture.py` keeps a baseline and **fails when things get worse**. Paying off
inherited debt is gradual; adding new debt is not allowed.

1. **The entrypoint exports nothing.** `app/main.py` assembles and runs. Infrastructure
   (connections, credentials, clients) lives in its own modules. No module imports `app.main` —
   that pattern gave MYRMIDON 10 import cycles, 54 lazy imports and one live deadlock that stopped
   the whole execution loop.
2. **Imports at the top of the file.** Exceptions (heavy model load, temporary cycle break) carry
   a `# lazy: reason` marker; the checker counts those separately.
3. **One database, one access style.** DAEDALUS owns the schema. Other services read through
   internal HTTP endpoints, not SQL against tables they do not own (MYRMIDON currently has 20 raw
   `text()` calls and zero model imports — a rename breaks it silently).
4. **Routers stay thin** — parse, call a service function, format the reply. No DB or Redis in a
   handler body. Practical limit ~300 lines per router file; the rest goes to `app/services/`.
5. **A function fits in your head** — complexity ≤ 15, ≤ 8 arguments, ≤ 60 statements (ruff
   enforces). `assemble_mission_prompt` at 90 is the standing counter-example.
6. **Handle the error or let it out.** `except Exception: pass` is banned; catch a concrete class,
   log it, continue meaningfully, and re-raise with `raise ... from exc`. The deadlock left no log
   line precisely because of a silent catch.
7. **Config in one place** — `app/config.py` per service. No `os.getenv` elsewhere (there are 262
   calls across 150 variables today).
8. **No `global`.** State is created at startup and passed explicitly; daemon threads make hidden
   module state a race you cannot reproduce.
9. **Every live defect gets a test at its own level** — logic → unit, service interaction →
   integration. MYRMIDON is at 8 % coverage and publishes to real channels; that is the gap to close.
10. **Schema changes are migrations** (alembic), never a hand-written `ALTER TABLE` at startup.
11. **Comments explain WHY**, with the measurement that motivated the decision. This is already the
    project's strongest habit — keep it.

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
- **A mission that argues with itself is not a typo.** `narrative_goal`, `stance` and `our_side` all
  expressed "what we argue" with no rule about precedence, so «За аргентину» (goal «должна была
  выиграть», stance «проиграл из-за тренера») published a comment agreeing with the defeat.
  `mission_validate.py` catches it on save. Two measured facts about that check: asking the model
  «непротиворечиво?» as JSON makes it answer `false` for EVERYTHING (3/6, and only because half the
  cases were contradictory) — ask for a **direction** («за»/«против») instead, 5/6; and the prompt
  MUST state that «A лучше, чем B» is support for A, or every comparative stance is flagged as
  opposition (3/3 false positives on a correct mission).
- **Tone must be measured AFTER our entry, not over the whole thread.** Measured in the polygon on a
  real 21-comment thread: one comment, then a coordinated three, left the aggregate verdict at
  OPPOSE every time. Three replies in 24 cannot move an average — a whole-thread reading is blind to
  the intervention it exists to judge and would report "no effect" for any implementation ever built.
- **Telegram's `is_self` means "the reading session's own messages", not "ours".** A thread exported
  under the alpha shows beta and gamma as strangers, so engagement counted the swarm answering
  itself. Use `outcome_engine.swarm_identities` (all accounts' ids + usernames, cached a day).
- **The execution delay is slept in the single consumer loop.** One task blocks every agent and every
  channel for its full pacing delay. Four junk tasks targeting `"Self"` (from `gamma_noise`, written
  for the dead mobile "post to your own feed" path) held a real mission comment behind 36 minutes.
  Targets are validated BEFORE the sleep — and note `parse_target("Self")` returns a truthy ref with
  no post id, so a comment task must be checked on the PAIR. Per-agent pacing is still open.
- **Recon must be lexical-first, and IDF-weighted.** Embedding-first retrieval for the transport
  mission returned Novorossiysk, Trump/Ukraine, weightlifters and a fire; a plain share-of-words test
  then admitted articles reusing «развитие» and «город». Recon runs once per mission, so it scans.
  A recon that files nothing returns `missing_terms` — the mission's own words the corpus never
  mentions — which is the actionable half.
- **A mission needs an explicit side.** With only free-text `narrative_goal` + `stance` the model
  guesses whose side it is on — and a contradiction between them (goal «Аргентина должна была
  выиграть» vs stance «Аргентина проиграла из-за тренера») yields comments arguing against the
  mission's own goal. Fill `our_side` / `opponent` / `key_points` / `red_lines`.
- **Ask the model to QUOTE, not to judge.** «Найди главный довод против нас, или ответь НЕТ, если
  никто не возражает» returned «НЕТ» **2/2** on a thread that plainly opposed us — the same refusal
  reflex that made the old relevance gate say «нет» 50/50. «Кто здесь спорит с нашей позицией и
  какими словами? Процитируй» returned the strongest opposing line 2/2, stably. Copying is far
  easier for a 3B model than deciding, so "is there an objection at all" is answered by the mood
  classification instead, and the quote is then verified against the thread (an argument nobody
  made is a hallucination — the same rule as `places_in_text`).
- **IDF cannot tell a topic from an ordinary word on a small scattered corpus.** Over 1594 facts
  «развит» appears in 72, «трансп» in 54, «людей» in 21 — so a sum of weak matches always beats a
  couple of strong ones. Three admission rules were measured in turn (share of the mission's
  weight; normalisation by the top-3; two rare terms) and each ranked junk first: Samarkand's
  hectares, a newspaper's anniversary, a boat sinking in Zimbabwe, mushrooms by the roadside.
  What works is naming the subject with the model and requiring the article to be ABOUT it.
- **A single subject word is regularly a homonym.** «трафик» admitted footfall in electronics
  shops, «развяз» admitted «развязать войну». Hence ≥2 distinct subject words or ≥3 mentions.
- **Judge the crowd, not yourself.** Mood and objection must be read over the thread WITHOUT our
  own comments: measured in the polygon, after two runs nine of ours against eight of theirs
  flipped the verdict to AGREE, and the extractor would have quoted a teammate as the opponent.
- **Nothing cheap catches fluent nonsense.** The model sometimes emits fluent gibberish in a mixed
  Turkic language under a Russian post, and it passes every guard. Measured and rejected: a
  "Russian function words" rule (the gibberish scores 0.121 while 27 of 57 real comments score
  0.000 — they are legitimately in English and Uzbek), and language identification
  (`lingua` has neither Uzbek nor Kyrgyz among its 75 languages, calls the gibberish `slav` with
  0.837 confidence, and would reject the 9-in-50 real cases where a human answers in Uzbek under a
  Russian post — normal in Tashkent channels). This is the model's ceiling; a bigger
  `TEXT_MODEL_NAME` is the fix, not another heuristic.
- `app/main.py` registers signal handlers at import — guarded to main-thread only (daemon
  threads re-import it).
- Mission DAG reconciler is legacy; it only touches `running`/`amplifying` so new
  `active`/`paused` missions are safe.
