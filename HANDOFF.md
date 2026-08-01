# MORPHEUS — Session Handoff (for a fresh agent)

> You are resuming work on MORPHEUS with **no prior chat memory**. Read this file fully,
> then read **`CLAUDE.md`** (architecture + hard rules), **`README.md`** (overview),
> **`walkthrough.md`** (per-stage log), **`CHANNEL_PROFILING.md`** (the profiling subsystem
> design), and **`DAEDALUS_CAPABILITIES.md`** (the console's full screen↔endpoint↔data map).
> After reading, **reply to the operator in RUSSIAN** (code, logs, code comments and git commit
> messages stay in English; the operator console UI is in Russian).
>
> **Current phase:** the DAEDALUS **UI redesign is COMPLETE** (Mantine 7, Stages 65–77). Stage 38
> completed the first functional reliability pass: relevance, RAG, target health and explicit
> mission position. The next focus is the remaining swarm throughput and knowledge coverage. See §2.
>
> **Git:** branch `stage-21-22-rag-engine` (a WIP feature branch — never commit to `master`).
> HEAD at handoff time = Stage 77. Working tree is clean. Stages are tagged in
> commit subjects; full history is in `git log`.

---

## 1. Architecture Snapshot

MORPHEUS is an autonomous social-influence swarm on **Telegram** (Pyrogram MTProto, real
userbot accounts). Persona bots ("souls") read channels, comment cognitively, hold multi-turn
conversations with real humans, gather news into a RAG knowledge base, and coordinate as an
alpha/beta/gamma caste hierarchy to push **Missions** (permanent narrative goals). One operator
drives it from the **DAEDALUS** web console.

> The mobile/Appium path (Instagram / Threads / YouTube) is **broken and out of scope** (host
> ADB at `host.docker.internal:5037` refused; AVD orchestrator builds invalid container names).
> Twitter is not integrated. Do NOT rabbit-hole on these unless explicitly asked.

### Containers (docker-compose.yml) — all 8 currently UP

| Service | Build/Image | Port | Role |
|---|---|---|---|
| **postgres** | pgvector/pgvector:pg16 | 5432 | Relational store **+ pgvector** (RAG embeddings) |
| **redis** | redis:7-alpine | 6379 | Queues, telemetry stream, locks, cooldowns, markers |
| **muninn** | ./muninn | 8002 | Long-term **dialog memory** (embedded ChromaDB, FastAPI) |
| **daedalus** | ./daedalus | 8000 | **Control plane**: FastAPI API + React SPA (built into the image) |
| **orpheus** | ./orpheus | 8001 | **Cognitive core**: Redis worker; NO HTTP for generation |
| **myrmidon** | ./myrmidon | (8003 int.) | **Execution swarm**: Pyrogram (TG) + the autonomous engines |
| **heimdall** | ./heimdall | 8004 | **Speech-to-text** service (faster-whisper, CPU/int8) |
| **huginn** | ./huginn | — | Legacy RSS/web scrapers (its TG scraper is dead Telethon — unused) |

**External dependency:** host **Ollama** at `host.docker.internal:11434` — models
`qwen2.5:3b` (text generation), `nomic-embed-text` (RAG embeddings), `moondream` (VLM / image
descriptions). The single **~6 GB GPU runs ONE model at a time** (ORPHEUS unloads with
`keep_alive=0`) — keep LLM calls cheap and serial. HEIMDALL runs faster-whisper on **CPU**
(`STT_MODEL`, default `medium`; set `large-v3` in prod). MYRMIDON has **Tesseract OCR**
(rus/ukr/kaz/kir/tgk/uzb/uzb_cyrl) for text-in-image posts.

### How the services interact (data flow)

- **Redis is the bus.** Queues: `queue:raw_events` (HUGINN→ORPHEUS, legacy autonomous),
  `queue:execution_tasks` (engines→MYRMIDON), `queue:mission_gen` (MYRMIDON↔ORPHEUS
  request/reply, replies on `reply:missiongen:<id>` and `reply:relevance:<id>`). Telemetry:
  `stream:agent_events` (capped stream the Live Ops feed tails).
- **ORPHEUS has NO HTTP for generation** — it's a Redis worker (`BRPOP` on
  `queue:raw_events` + `queue:mission_gen`). MYRMIDON pushes a request and blocks on the reply
  key. ORPHEUS calls host Ollama for generation/classification, MUNINN for memory, DAEDALUS for
  RAG (`/knowledge/internal/rag-search`).
- **MYRMIDON** runs daemon-thread engines: `target_engine` (primary driver, polls every 300s),
  `dialogue_engine` (polls dialogue watches). It reads/posts on Pyrogram sessions, calls
  HEIMDALL (audio→text), Ollama (image→text, VLM), and DAEDALUS internal endpoints (ingest
  knowledge, suggest targets, build channel profiles, log decisions).
- **DAEDALUS** owns Postgres + the React SPA. Internal endpoints (header `X-Internal-Token`,
  default `morpheus-internal-sync-key`): `/knowledge/internal/{ingest,rag-search,by-geo}`,
  `/souls/internal/profiles`, `/analytics/internal/activity`, `/missions/internal/suggest-target`,
  `/channels/internal/{profile,themes,profile}`, `/decisions/internal/log`. A background
  **reconciler thread** runs every 8 s (`mission_control`): legacy DAG reconcile + the new
  `reconcile_dynamic_rosters`.

### The live mission pipeline (one MYRMIDON `target_engine` tick, per active mission)

1. **Channel profiling FIRST** (`_profile_channels_for_mission`, hybrid cadence, Redis-gated):
   heavy profile daily, hot themes ~4 h. So relevance always has fresh channel context.
2. **Alpha seeding** (`_process_mission`), gated by `account_health.in_cooldown` AND
   `schedule.in_active_hours` (persona's active window, Tashkent UTC+5). Scans the mission's
   `active` channel targets; **media-only posts are now included** and "read" first
   (`read_media_context` → `media_reader`: audio→HEIMDALL STT, image→Ollama VLM **+ Tesseract
   OCR** for text cards), peeks at their discussion threads, and skips targets freshly known as
   blocked. ORPHEUS judges whether the persona can naturally **join the conversation** in channel
   context (`ДА` / `СЛАБО` / `НЕТ`, `penalties=False` — see Constraints); the engine ranks all
   viable posts by verdict, live-thread size and freshness. On a viable post + rate-ok: seeds a comment. ORPHEUS picks a
   **dynamic per-post tactic** (post+thread mood vs stance), weaves persona + RAG + MUNINN
   memory + thread mood + **media context** + **channel context (+ region news)** + explicit mission position,
   with anti-echo and **anti-repeat** (against the agent's own recent comments). Posts via
   Pyrogram, registers a dialogue watch. Every relevance verdict and rate-skip is written to
   `decision_events` (`_log_decision`) and emitted to Live Ops (`media_read`/`relevance`/
   `rate_skip`).
3. **Swarm amplification** (`swarm.py`): beta = cheap "lite" comment, gamma = emoji reaction —
   companions scoped to the mission roster AND filtered by `schedule.in_active_hours`.
4. **Agent target suggestions** (`_suggest_targets_for_mission`): roster bots propose channels
   they read as new `MissionTarget`s (`status='suggested'`).
5. **Conversations** (`dialogue_engine`): polls watches → human reply → ORPHEUS reply-mode →
   MYRMIDON answers (multi-turn, gated by active hours).

### Key Postgres tables
RBAC: `admin_users`, `roles`, `role_permissions`, `user_roles`. Identity: `agent_profiles`
(persona; `caste`, `status`, `core_mission`, `core_interests`, `context_subscriptions`,
`active_hours_start`/`_end`), `souls_accounts`, `profile_history`. Channels:
`agent_channel_prefs`, **`channel_profiles`** (per-channel: geo_layers/geo_label/topics/
recent_themes/summary). Missions: `missions` (`stance`, `status` active|paused, `agent_mode`,
`dynamic_count`, `tactic` default `dynamic`, explicit `our_side` / `opponent` / `key_points` /
`red_lines`), `mission_targets` (including health `unknown` / `ok` / `blocked` / `degraded`),
`mission_squads`. Knowledge:
`knowledge_facts` (pgvector RAG; `landscape_layers` global/regional/state/city, `categories`,
`tags`). Activity: `agent_activity_logs` (comment|reply|react), **`decision_events`** (durable
why-did/didn't-react: kind relevance|skip|comment, detail, verdict).

### Key Redis keys
`stream:agent_events` (Live Ops). `morpheus:dialogue:watches`/`:handled`.
`morpheus:target:lastseen` (hash) / `:rate:*` (hourly caps). `morpheus:suggest:checked:<mid>:<id>`.
`morpheus:recent_outputs:<agent>` (anti-repeat). `morpheus:profile:heavy:*` (24 h) / `:themes:*`
(4 h). `morpheus:tg_lock:<agent>`, `morpheus:tg_cooldown:<agent>`, `morpheus:amplified:<url>`.

---

## 2. Current status — UI is DONE; next focus is the FUNCTIONAL / swarm side

### DAEDALUS UI redesign — ✅ COMPLETE (Stages 65–77)
The whole operator console was rebuilt as a professional command-and-control center on **Mantine 7**
(see `CLAUDE.md` → DAEDALUS React section, and `walkthrough.md` for the per-stage log). Highlights:
Mantine `AppShell` (fixed nav + single scroll), entity routing `#/<view>/<id>`, reusable `src/ui/`
primitives (`DataView`/`DetailPage`/`EntityPicker`/`StatTile`), **full-screen master→detail editing of
every entity** (no modals/drawers), **pick-from-list everywhere** (no typed IDs), Dashboard v2,
relationship cross-links, h-scroll tables. Every screen is Mantine; **no per-component CSS remains**
(only `App.css` theme vars). All 13 pre-Mantine components were deleted. `DAEDALUS_CAPABILITIES.md` is
the authoritative screen↔endpoint↔data map. **Treat the UI as complete — only touch it for real
operator-reported issues; do not re-litigate it.**

### ▶ NEXT FOCUS: the functional / swarm logic
The operator's next phase is the **functional core**, not the UI. The architecture, data flow, the
live mission pipeline, data model and Redis keys are all in **§1 above** — read it; that is the map for
functional work. The functional code lives in:
- **ORPHEUS** `orpheus/app/` — cognition (Redis worker; `main.py` handlers, `persona.py` prompt
  assembly, `rag.py`, `guardrails.py`, `media_enricher.py`). NO HTTP for generation.
- **MYRMIDON** `myrmidon/app/` — execution (`target_engine.py` primary driver, `dialogue_engine.py`,
  `swarm.py`, `drivers/tg_client.py`, `media_reader.py`, `schedule.py`, `account_health.py`).
- **DAEDALUS** `daedalus/app/` — APIs + engines feeding the swarm (`channel_profiler.py`,
  `classifier.py`/`embeddings.py`, `genesis_engine.py`, `mission_control.py`, internal endpoints).
- **HUGINN / MUNINN / HEIMDALL** — scrapers / dialog memory / STT.

**Known functional levers & open issues (candidates — confirm direction with the operator first):**
- `qwen2.5:3b` is weak: parrots input + rehashes its own comments (mitigated by `guardrails.is_echo`/
  `is_repeat` + `morpheus:recent_outputs`). A larger `TEXT_MODEL_NAME` would sharpen comments/relevance;
  prompts and guards are model-agnostic. The single ~6 GB GPU runs ONE model at a time.
- Relevance/tactic are short classification calls → MUST pass `generate_text(..., penalties=False)` or
  the model emits garbled tokens (the `'дятьнет'` bug).
- Channel Profiling is built (see `CHANNEL_PROFILING.md`); relevance is judged in channel context.
- Conversations (`dialogue_engine`) are multi-turn; quality depends on persona + memory.
- Mobile/Appium path is **broken & out of scope** (Devices/Sandbox/CloneFactory mobile bits).

### Stage 38 — completed reliability pass
- **Relevance:** the gate cleans post/media input, ignores OCR schedule dumps, sees a bounded live
  thread and returns a graded joinability verdict. Keyword recall may lift `НЕТ` only to `СЛАБО`;
  channel affinity is a tie-breaker, never permission to accept all posts.
- **RAG:** the query joins the situation with mission goal/stance; vectors fetch candidates but
  lexical overlap admits facts. Stored HTML is scrubbed on ingest; `/knowledge/facts/cleanup`
  repairs historical rows and re-embeds them.
- **Target health:** MYRMIDON probes new targets, reports `unknown` / `ok` / `blocked` /
  `degraded` to DAEDALUS, and retries a blocked target only after its re-check window. A
  comment-disabled **post** is not a blocked channel; guest-send errors join then retry.
- **Mission position:** `our_side`, `opponent`, `key_points`, `red_lines` travel from the mission
  editor to the prompt so the model need not infer its side. Full evidence: `DIAGNOSIS.md`.

### How to run / verify (functional)
- Deploy a change: `docker compose build <svc> && docker compose up -d <svc>`. ORPHEUS/MYRMIDON are
  Redis workers / daemon threads — after restart the profile cache + loops take **~30 s** to warm;
  wait before asserting failure. **Don't rebuild a service you didn't change.**
- **Live swarm = real Telegram posts.** 3 real accounts: `clone_alpha_91eea738` (alpha),
  `clone_alpha_bd35bcad` (beta), `clone_alpha_0e795b8d` (gamma). Test channel `@tashkent_news333`.
  Mission **#10** ("Поддержка общественного транспорта") is **active** with a full roster — the engine
  keeps working it (throttled ≤1 comment/channel/hr, ≤4/agent/hr, only in active hours 8–22 Tashkent).
  Pause an agent/mission via the UI to stop it. Be considerate — every test comment is real.
- Operator login: user `morpheus`, password from `.env` `SUPERADMIN_PASSWORD` (dev default
  `CHANGE_ME_IMMEDIATELY`). For UI checks: `fetch('/api/v1/auth/login', form-encoded)` →
  `localStorage.daedalus_token`, reload, resize ~1440px. Login endpoint is **form-encoded**, not JSON.

## 3. Constraints & Rules (HARD — do not violate)

- **Language:** reply to the operator in **Russian**. Code, logs, code comments, commit
  messages in English. The operator console UI strings are in Russian.
- **Commits:** commit **only when the operator says "коммит"/"commit".** On that trigger, FIRST
  update `README.md`, `CLAUDE.md`, `walkthrough.md` (and this `HANDOFF.md`) to reflect the
  batch, THEN `git add -A` and commit. Use the `Stage NN: …` subject convention. The operator
  often says "коммит. потом продолжим" (commit, then continue). Never commit to `master`.
- **Secrets/artifacts:** `.env` is gitignored (holds `TG_API_*`, `SUPERADMIN_PASSWORD`, DB
  creds). NEVER stage `*.png` screenshots, `.playwright-mcp/`, `*.session`. Delete temp
  diagnostic scripts after use. Always screen `git status` before committing.
- **Frontend build:** React SPA is built **inside the daedalus image** (`npm run build` →
  `app/static/`). Any `.tsx`/`.css` change needs `docker compose build daedalus`. The build runs
  `npm install` (network available) then **`tsc` in strict mode — unused variables FAIL the
  build** (e.g. `TS6133`). Deps are minimal: react, react-dom, lucide-react (no router/UI lib);
  routing is a homegrown hash hook in `App.tsx`. To add a screen: nav button + add to the
  `VIEWS` list/union + mount it in `App.tsx`.
- **Deploy:** `docker compose build <svc> && docker compose up -d <svc>`. ORPHEUS/MYRMIDON are
  Redis workers/daemon threads — after a restart the profile cache/loops take **~30 s** to warm;
  wait before asserting failure.
- **Verify on real data.** Operator login: user `morpheus`, password from `.env`
  `SUPERADMIN_PASSWORD` (dev default `CHANGE_ME_IMMEDIATELY`). For UI checks: navigate with
  Playwright, then in-page `fetch('/api/v1/auth/login', …)` and `localStorage.setItem(
  'daedalus_token', token)` (the login form resists automation), reload, resize ~1440px. You can
  now deep-link any view via the hash (`#/swarm`, `#/missions`, …).
- **Live swarm = real posts.** 3 real TG accounts: `clone_alpha_91eea738` (alpha),
  `clone_alpha_bd35bcad` (beta), `clone_alpha_0e795b8d` (gamma). Test channel
  `@tashkent_news333`. Mission **#10** ("Поддержка общественного транспорта") is **active** with
  a full roster — the live engine posts to a real channel (throttled ≤1/channel/hr, ≤4/agent/hr,
  and only in active hours 8–22 Tashkent). Pause an agent/mission to stop it.
- **Model gotchas (`qwen2.5:3b` is weak):** it parrots input (`guardrails.is_echo`) and rehashes
  its own past comments (`guardrails.is_repeat` + `morpheus:recent_outputs`). **CRITICAL:** the
  anti-parroting `repeat_penalty`/`frequency_penalty` CORRUPT short YES/NO classification (gave
  garbled `'дятьнет'`) — always call `generate_text(prompt, penalties=False, temperature≈0.2)`
  for relevance/tactic classification. **Moondream VLM cannot read text in images** (it
  hallucinates a scene) → use Tesseract OCR for text cards; keep VLM prompts SHORT (it returns
  empty on long/structured prompts). A bigger `TEXT_MODEL_NAME` would sharpen everything (guards
  are model-agnostic).
- **Pyrogram gotchas:** one session per account → a per-agent **session lock**; use a **fresh
  event loop per call** (`tg_client._run`) or daemon threads crash with "got Future attached to
  a different loop". Prefer `@username` over cold `-100…` ids; reply via `message.reply_text()`.
  Discussion replies often have `from_user=None` (anonymous) — still real humans.
- **Tooling gotchas:** the Bash tool blocks foreground `sleep` (use `until <check>; do sleep N;
  done`); the working directory **persists between Bash calls** (a stray `cd` will break later
  relative paths — prefer absolute paths). `docker logs --since <window>` won't match a line
  older than the window (this caused a stuck until-loop earlier).

---

## Appendix — where things live (quick map)
- ORPHEUS: `orpheus/app/main.py` (`handle_mission_generation`, `handle_relevance`,
  `_resolve_dynamic_tactic`, `_channel_context`, `generate_text(...,penalties=)`),
  `persona.py` (`assemble_mission_prompt`, `build_channel_block`, `build_mood_prompt`/
  `tactic_from_mood`), `guardrails.py` (`is_echo`/`is_repeat`), `rag.py`, `media_enricher.py`.
- MYRMIDON: `target_engine.py` (the primary engine — profiling, seeding, suggestions, decisions,
  `_geo_news_digest`), `drivers/tg_client.py` (`read_media_context`, `fetch_new_posts`,
  `execute_comment`, `_run`), `media_reader.py` (STT+VLM+OCR), `swarm.py`, `dialogue_engine.py`,
  `schedule.py` (active hours), `account_health.py`.
- DAEDALUS: `models.py`, `database.py` (`init_tables`; new tables auto-create, columns migrate in
  `_STAGE23_COLUMNS`), `mission_control.py`, `router_channels.py`, `router_decisions.py`,
  `channel_profiler.py`, `db_explorer.py`, `classifier.py`. React (Mantine): `App.tsx` (AppShell +
  `useHashRoute` `#/<view>/<id>`), `src/ui/` primitives (`DataView`/`DetailPage`/`EntityPicker`/
  `StatTile`), one `src/components/*Screen.tsx` per view. No per-component CSS (only `App.css`).
- Design doc for the profiling subsystem: `CHANNEL_PROFILING.md` (Phase 1 + 2 fully done).
