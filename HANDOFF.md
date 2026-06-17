# MORPHEUS — Session Handoff (for a fresh agent)

> You are resuming work on MORPHEUS with **no prior chat memory**. Read this file fully,
> then read **`CLAUDE.md`** (architecture + hard rules), **`README.md`** (overview),
> **`walkthrough.md`** (per-stage log), and **`CHANNEL_PROFILING.md`** (the profiling
> subsystem design). After reading, **reply to the operator in RUSSIAN** (code, logs, code
> comments and git commit messages stay in English; the operator console UI is in Russian).
>
> **Git:** branch `stage-21-22-rag-engine` (a WIP feature branch — never commit to `master`).
> HEAD at handoff time = Stage 70. Working tree is clean. Stages are tagged in
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
   OCR** for text cards). ORPHEUS judges **relevance IN the channel's profile context**
   (`penalties=False` — see Constraints). On YES + rate-ok: seeds a comment. ORPHEUS picks a
   **dynamic per-post tactic** (post+thread mood vs stance), weaves persona + RAG + MUNINN
   memory + thread mood + **media context** + **channel context (+ region news)** + stance,
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
`dynamic_count`, `tactic` default `dynamic`), `mission_targets`, `mission_squads`. Knowledge:
`knowledge_facts` (pgvector RAG; `landscape_layers` global/regional/state/city, `categories`,
`tags`). Activity: `agent_activity_logs` (comment|reply|react), **`decision_events`** (durable
why-did/didn't-react: kind relevance|skip|comment, detail, verdict).

### Key Redis keys
`stream:agent_events` (Live Ops). `morpheus:dialogue:watches`/`:handled`.
`morpheus:target:lastseen` (hash) / `:rate:*` (hourly caps). `morpheus:suggest:checked:<mid>:<id>`.
`morpheus:recent_outputs:<agent>` (anti-repeat). `morpheus:profile:heavy:*` (24 h) / `:themes:*`
(4 h). `morpheus:tg_lock:<agent>`, `morpheus:tg_cooldown:<agent>`, `morpheus:amplified:<url>`.

---

## 2. Status of Stage 47 & 48

### Stage 47 — Runtime dynamic roster auto-assign — ✅ DONE, committed (`c695241`)
The DAEDALUS reconciler (8 s loop) now also fills the roster of every `active` mission with
`agent_mode='dynamic'` up to its `dynamic_count`. `mission_control.reconcile_dynamic_rosters`
(≥1 alpha + beta/gamma split via `_dynamic_role_counts`, best-match by caste↔role + topic
overlap, additive only). Also fixed `ACTIVE_MISSION_STATES` to include `active` (the per-bot
mission cap now counts permanent missions). **Verified live**: a dynamic mission with no squad
auto-filled with caste-matched alpha/beta/gamma in ~16 s. Nothing broken.

### Stage 48 — UX/UI overhaul, Phase 1 (foundation + critical bugs) — ✅ DONE, committed (`22501c2`)
The operator asked to bring the **whole Daedalus console** to a real "mission center" standard.
This is a **multi-phase effort; only Phase 1 is done.** What Phase 1 delivered (all verified
live, nothing broken):
- **HTTP 400 in Database Explorer FIXED.** `db_explorer.py` `_validate_table_name(table, db)`
  now validates against the **live** list of public tables (the same source the UI uses), not a
  stale 8-table whitelist. Every table opens; bogus names still 400 (injection-safe).
- **Refresh no longer snaps to Dashboard.** `App.tsx` now uses **hash routing** (`#/view`):
  the active tab is in the URL, so refresh keeps it, links are shareable, back/forward work.
  Deep-link example: `http://localhost:8000/#/database`.
- **Reusable `components/DataTable.tsx` (+ css)**: search, sortable columns, pagination,
  loading/empty states. **`DecisionLog` ("Решения") migrated to it** as the reference pattern.

### Stage 49–53 — UX/UI overhaul Phase 2 (uniform lists) — ✅ COMPLETE, committed
Migrated six list surfaces to `DataTable`: **`AccountsManager`** (search-less card grid + English
title → searchable/sortable/paginated table, unified `view-container` header, detail pane preserved
on row-click, fully Russified), **`ChannelProfiles`** (hand-rolled `<table>` → `DataTable`, gains
sort + pagination, rich cells preserved via `render`), **`LandscapeManager`** (hand-rolled
`<table>` → `DataTable`; sort by id/platform/type/status; layer pills + status toggle + edit/delete
preserved; whole screen incl. its add/edit modal English → fully Russified — modal kept, only
strings changed), **`ScoutingRadar`** (search-less card grid with **684 rows** → `DataTable` with
search + sort by velocity/engagement/time + pagination; heat metaphor kept as a heat-colored
"Скорость" badge column; convert/dismiss + toasts kept; English → fully Russified), and
**`NewsHubInspector`** (card stream + a **fake telemetry panel** → full-width `DataTable` with
search + sort + status filter; removed the dishonest panel with fabricated metrics, raised fetch
20→200, kept live/pause + edit/reject/approve + edit modal; English → fully Russified; **also
fixed** the modal layer-checkbox key-case bug (`Global`→`global`) and the unlabeled `Processed`
status), and the **`SwarmDashboard` drill-down modals** (the activity list — up to 150 rows/24 h —
and the dialogues list → `DataTable` inside the modal with search/sort/pagination; long comment
text clamped to 4 lines in-cell). All verified live. **Two screens deliberately NOT migrated:**
`MuninnExplorer` (already has server-side search + layer filters + pagination) and `DeviceGrid`
(a control dashboard — per-card live telemetry on `<canvas>`, VNC, hardware controls, emulator
provisioning — for the **out-of-scope broken mobile/Appium stack**, not a list). A `DataTable`
would regress both. **Phase 2 (uniform lists) is COMPLETE.**

### Stage 54–58 — UX/UI overhaul Phase 3 (de-modal editors) — ✅ COMPLETE, committed
Built the reusable **`components/SidePanel.tsx`** (+ css): a non-blocking editor that slides in from
the right with NO dimming backdrop (rest of page + sidebar stay clickable; submit button goes in the
footer, bound to the form via the HTML `form=` attribute — or plain `onClick` if the editor isn't a
`<form>`). Verified live: unsaved edits survive a tab switch; the panel hides with its host view
(`display:none`) so it doesn't bleed over other screens. **All editors converted:** `LandscapeManager`
(add/edit source), `MuninnExplorer` (inject fact), `NewsHubInspector` (edit event), `ChannelManager`
(account channels — wide 680px tabs/filters/bulk panel), `MissionDeck` (create form + tabbed
`MissionDetail` editor — its agent roster is already a pick-from-list, nothing to replace),
`SoulsContext` (the big 5-tab profile editor → wide panel). The only leftover `modal-overlay` is the
read-only `SwarmDashboard` drill-down (activity/dialogue viewer — not an editor, so the unsaved-edits
complaint doesn't apply; convert it only as an optional consistency pass).

### ⚠️ MAJOR RE-SCOPE (operator, after Stage 64) — read `DAEDALUS_CAPABILITIES.md` first
The Stages 49–64 work (DataTable migrations, SidePanel de-modal, Russification) was the **wrong
focus**. The real mandate is a **professional command-and-control / monitoring center**:
1. **Full UI-framework migration** → **Mantine 7** (adopted Stage 65: provider + dark theme + Mantine
   `AppShell`; the long-nav page-scroll bug is fixed).
2. **Full-screen master→detail edit per entity** (NOT modals, NOT drawers). The `SidePanel`s built in
   Stages 54–58 are to be **replaced** by routed full-screen detail pages exposing **all** params of
   the selected item — for accounts, souls, landscape, news hub, knowledge, channel profiles, AND all
   others.
3. **Pick-from-list everywhere** — every "type an ID" (device→agent, etc.) → searchable/filterable/
   sortable list with detailed item data.
4. **Dashboard v2** — serious, information-dense (trends/queues/throughput/health, drill-through).
5. Far more **informativeness / interactivity** + relationship cross-links on every tab.
6. Bug fixed (Stage 66): Database Explorer table now scrolls horizontally.

`DAEDALUS_CAPABILITIES.md` is the authoritative inventory of all 20 screens + ~90 endpoints + the
redesign mandate/sequence. **It is the source of truth for the redesign.**

### Operator working mode (IMPORTANT)
Do **NOT** stop+commit after every small change. **Batch** the redesign and keep working until a large
coherent slice is done, then report. (This reverses the earlier per-screen "коммит. потом продолжим"
cadence.)

### Redesign progress (Stage 67 done) + next steps
**DONE (Stage 67):** per-entity routing (`useHashRoute`, `#/<view>/<id>`); reusable **`src/ui/`**
primitives — `DataView`, `DetailPage`, `EntityPicker`, `StatTile`; **`SoulsScreen`** (flagship
full-screen 5-tab editor, replaces `SoulsContext`); **`AccountsScreen`** (full-screen + soul-bind via
`EntityPicker`, replaces `AccountsManager`); **Dashboard v2** ("Центр управления", KPI tiles +
sparklines). All verified live. The old `SoulsContext.css`/`AccountsManager.css` are imported in
`App.tsx` to keep global classes (`.status-badge`/`.tabs`/`.modal-*`/`.header-row`) for un-migrated
screens.

**Redesign core COMPLETE (Stages 67–70).** Migrated to full-screen `DataView`+`DetailPage`
(+`EntityPicker`): **Souls, Accounts, Missions, Landscape, News Hub, Knowledge, Channel Profiles,
Decisions, Activity**; **Dashboard v2**; **Genesis + Auth Factory** rebuilt from raw HTML
(Auth = Mantine Stepper wizard); **Devices** binding via `EntityPicker`. All verified live.

**Remaining (functional, low priority / optional):**
1. `DatabaseExplorer` — h-scroll bug fixed; full Mantine-Table reskin is optional polish.
2. `SwarmDashboard` / `ScoutingRadar` — still old `DataTable` (work); migrate to `DataView` +
   full-screen detail for consistency if desired.
3. `LiveOps` / `CloneFactory` / `SandboxConsole` — functional; reskin only if asked.
4. **Relationship cross-links** — weave account↔soul↔mission↔channel↔decisions navigation into the
   new detail pages for more informativeness.
5. **Cleanup:** once all screens are migrated, delete the dead old components (`SoulsContext`,
   `AccountsManager`, `MissionDeck`, `NewsHubInspector`, `MuninnExplorer`, `ChannelProfiles`,
   `LandscapeManager`, `DecisionLog`, `ActivityStream`, `DataTable`, `SidePanel`) — but FIRST move
   their **global** CSS (`.status-badge`, `.tabs/.tab-btn`, `.modal-*`, `.header-row`, `.data-grid`,
   `.layer-pill`, `.tag-*`) into a shared stylesheet (they're re-imported in `App.tsx` for now).
Templates: `SoulsScreen.tsx` / `AccountsScreen.tsx` / `MissionsScreen.tsx` + `src/ui/*`. Build
`daedalus`, verify via Playwright.

Stack screens are running; **do NOT rebuild unless you changed that service.** A frontend change
requires `docker compose build daedalus` (React SPA built inside the image; `npm install` runs in the
build so adding Mantine deps to `package.json` is enough).

---

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
  `channel_profiler.py`, `db_explorer.py`, `classifier.py`. React: `App.tsx` (hash routing),
  `components/DataTable.tsx` (reusable table), plus one component per screen.
- Design doc for the profiling subsystem: `CHANNEL_PROFILING.md` (Phase 1 + 2 fully done).
