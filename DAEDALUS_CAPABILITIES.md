# DAEDALUS — Capability & Functionality Inventory

> **Purpose of this doc:** an authoritative, complete map of *everything* the DAEDALUS control
> plane can do — every operator-facing screen, every backend endpoint, the data behind it, and
> the operator actions it enables. This is the foundation for the **UI redesign** into a
> professional command-and-control / monitoring center. Last fully synced: **Stage 65** (Mantine
> shell migration in progress).
>
> Conventions: `🟢 operator` = used by the web console; `🔒 internal` = called by the swarm
> services (header `X-Internal-Token`), NOT for the UI; `📵 mobile` = part of the broken,
> out-of-scope Appium/AVD path (telemetry usually empty). Full API base = `/api/v1`.

---

## 0. What DAEDALUS is

DAEDALUS is the **control plane** of MORPHEUS: a FastAPI backend + React SPA (built into the
image) that owns Postgres (+pgvector) and is the single console from which one operator drives an
autonomous Telegram influence swarm. It does **not** generate text or post (that's ORPHEUS /
MYRMIDON) — it **configures, commands, observes, and audits** the swarm, and serves the RAG
knowledge + channel-profile context the cognitive core reads.

**Auth:** JWT. `POST /auth/login` (form), `GET /auth/me`. **RBAC:** User → Roles → atomic
permissions: `db:read`, `db:edit`, `monitoring:view`, `agents:manage`, `agents:view`,
`campaigns:{create,view,edit,delete}`, `accounts:{manage,view}`, `roles:{manage,view}`,
`system:settings`, `simulation:{view,manage}`. Enforced via `require_permission`. Roles/permissions managed at
`POST/GET /roles`, `GET /permissions`.

**Real-time substrate:** the console tails Redis telemetry (`stream:agent_events`) and live
analytics; most screens poll (2–10 s). Internal endpoints feed the swarm (knowledge ingest,
channel profiles, target suggestions, decision log, device status, activity).

---

## 1. Domains (screen → capabilities → API → data)

### 1.1 Dashboard / Swarm Overlord  🟢 `#/dashboard`
- **Purpose:** at-a-glance readiness + health of the whole swarm; the landing screen.
- **Capabilities:** GO/NO-GO readiness with blockers; fleet online %, active/total missions,
  scouted-last-hour + peak velocity, captured events + last prune, active accounts, radar
  pending + actions logged. Second tab: per-service **latency** diagnostics (DAEDALUS DB,
  ORPHEUS cache, HUGINN sync, MYRMIDON ADB).
- **API:** `GET /analytics/overlord`, `GET /analytics/latency`. (Also available, not yet
  surfaced: `GET /analytics/metrics`, `GET /analytics/queues`.)
- **Redesign gaps:** static cards, no trends/sparklines, no drill-through, no time range, no
  queue depths / throughput graphs. Should become the "mission control" hero screen.

### 1.2 Live Ops  🟢 `#/live`  ("Командный центр")
- **Purpose:** real-time per-agent activity feed + agent presence rail.
- **Capabilities:** live event stream (poll 1.2 s) with human-readable event labels
  (reading/thinking/posting/replying/amplifying/cooldown/…); per-agent liveness
  (working/idle/asleep); click an agent to filter; pause/resume; collapses repeated heartbeats.
- **API:** `GET /analytics/live` (agents + events, cursor `after`).
- **Redesign gaps:** no event detail panel, no per-agent timeline, no filtering by event
  type/service/status, no export.

### 1.3 Swarm Dashboard  🟢 `#/swarm`  ("Рой")
- **Purpose:** aggregate swarm KPIs with drill-down to the underlying rows.
- **Capabilities:** today/all-time counts (comments/replies/reactions), knowledge today/total,
  by-caste table, by-agent table, "now" panel (active dialogues, target/news channels, agents
  active/paused, success/fail 24 h). Click any number → drill-down list (activity / dialogues).
- **API:** `GET /analytics/swarm`, `GET /analytics/stream`, `GET /analytics/dialogues`.
- **Redesign gaps:** drill-down is a modal (should be a real view); no charts; no per-agent or
  per-mission breakdown navigation.

### 1.4 Accounts  🟢 `#/accounts`  ("Аккаунты")
- **Purpose:** the real TG/social accounts (access + hardware) and their binding to personas.
- **Capabilities:** list accounts (platform, username, status, device, bound agent); bind/unbind
  a floating soul; open the account's channels; per-account audit history.
- **API:** `GET /souls/accounts`, `PUT /souls/accounts/{id}/bind?agent_id=`,
  `PUT /souls/accounts/{id}/unbind`, `PUT /souls/accounts/{id}/assign`,
  `GET /souls/accounts/{id}/history`.
- **Data:** `souls_accounts` (platform, `auth_cookies`={session_string}, status), `account_audit_logs`.
- **Redesign gaps:** binding picks from a dropdown (ok), but no full account detail/edit screen;
  status/session health not editable; no proxy/device assignment here.

### 1.5 Souls (Vault)  🟢 `#/souls`  ("Души (хранилище)")
- **Purpose:** the personas ("souls") — full identity + behavior + mission stance editing.
- **Capabilities (editor tabs):** Identity (name, codename, city, profession, caste, platforms,
  active hours); Psychology (tone/vocab/emoji/aggression sliders, speech quirks, behavioral
  rules, min-delay / max-posts-per-hour); Mission (RAG layer subscriptions, core_mission text,
  per-topic stance modifiers); Accounts (bind/unbind accounts to this persona); History
  (versioned profile snapshots + **rollback**). Pause/resume agent. Live status dot per card.
  Search + filter by caste/status.
- **API:** `GET /souls/profiles`, `GET/PUT /souls/profiles/{agent_id}`,
  `POST /souls/profiles/{agent_id}/status`, `GET /souls/profiles/{agent_id}/history`,
  `POST /souls/profiles/{agent_id}/rollback/{history_id}`, `DELETE /souls/profiles/{agent_id}`,
  `POST /souls/genesis`.
- **Data:** `agent_profiles`, `profile_history`.
- **Redesign gaps:** the biggest editor — must become a **full-screen** detail page (per the new
  mandate), not a drawer; sliders need to be predictable; no diff view in history.

### 1.6 Soul Genesis  🟢 `#/genesis`  ("Генезис душ") — **raw HTML, needs rebuild**
- **Purpose:** generate a brand-new persona from a free-text concept (LLM).
- **Capabilities:** enter agent_id, codename, a concept prompt → genesis engine synthesizes a
  full persona. Currently bare HTML form, English.
- **API:** `POST /souls/genesis` (genesis_engine).
- **Redesign gaps:** rebuild on the component system; show the generated persona for review/edit
  before saving; informativeness (what fields the LLM filled).

### 1.7 Clone Factory  🟢 `#/factory`  ("Фабрика клонов")  📵
- **Purpose:** autonomous mass provisioning (boot AVDs, synthesize souls, register accounts,
  bind) — mobile path, out of scope/broken.
- **Capabilities:** count/caste/platform/vector-focus → launch job; per-bot pipeline stepper
  (Persona→Register→Bind→Done) + log; job summary.
- **API:** `POST /factory/mass-provision`, `GET /factory/jobs`, `GET /factory/jobs/{job_id}`.
- **Redesign gaps:** mobile-bound; keep but mark clearly; the stepper is a decent pattern to reuse.

### 1.8 Auth Factory  🟢 `#/auth`  ("Фабрика авторизации") — **raw HTML, needs rebuild**
- **Purpose:** log a real Telegram account into the swarm (MTProto): request code → verify →
  store session string. Also mobile session extraction.
- **Capabilities:** request login code for a phone, submit OTP (+ optional 2FA), persist the TG
  session as an account.
- **API:** `POST /auth-factory/telegram/request-code`, `POST /auth-factory/telegram/verify-code`,
  `POST /auth-factory/mobile/extract-session` 📵.
- **Redesign gaps:** raw HTML, English; this is a **critical real-action** flow → needs a clean,
  guided, informative wizard with clear state (code sent / awaiting OTP / bound).

### 1.9 Landscape  🟢 `#/landscape`  ("Ландшафт")
- **Purpose:** the scraping sources (channels/feeds/sites) HUGINN/engines harvest into knowledge.
- **Capabilities:** list/add/edit/delete sources (platform, type, target identifier, default
  landscape layers, tags, active toggle); force HUGINN sync.
- **API:** `GET/POST /landscape/`, `PUT/DELETE /landscape/{id}`, `POST /huginn/force-sync`.
- **Data:** `scraping_landscape`.
- **Redesign gaps:** add/edit is a side panel → mandate wants a full edit screen with all params;
  no per-source health / last-harvest / yield stats.

### 1.10 News Hub  🟢 `#/newshub`  ("Центр новостей")
- **Purpose:** captured raw events queued for ORPHEUS — inspect, edit, route, approve/reject.
- **Capabilities:** list captured events (platform, source, text/media, layers, status); edit
  text + routing status + layers; approve/reject; live/pause; status filter.
- **API:** `GET /huginn/captured-events`, `PUT /huginn/captured-events/{id}`,
  `POST /huginn/force-sync`. (🔒 `POST /huginn/internal/capture`, `PUT …/internal/capture/{id}`.)
- **Data:** `captured_raw_events`.
- **Redesign gaps:** edit is a side panel → full edit screen wanted; no source/geo analytics.

### 1.11 Knowledge (Знания роя)  🟢 `#/muninn`  ("Знания роя")
- **Purpose:** the RAG knowledge base (pgvector) ORPHEUS grounds comments on.
- **Capabilities:** browse/search facts (server-side, by layer + text); per-layer stat cards;
  inject a fact manually (content + layers + source) → auto-classify + embed + dedup; delete a
  fact. (Note: screen name says "MUNINN" but this is the **knowledge_facts** RAG store, distinct
  from the MUNINN dialog-memory service.)
- **API:** `GET /knowledge/facts`, `GET /knowledge/stats`, `POST /knowledge/facts/inject`,
  `DELETE /knowledge/facts/{id}`. (🔒 `POST /knowledge/internal/{ingest,rag-search}`,
  `GET /knowledge/internal/by-geo`.)
- **Data:** `knowledge_facts` (pgvector; layers/categories/tags/sources/cluster count).
- **Redesign gaps:** inject is a side panel → full screen wanted; no fact detail (cluster
  members, embeddings neighbors, which agents used it); no geo/category analytics.

### 1.12 Channel Profiles  🟢 `#/channelprofiles`  ("Профили каналов")
- **Purpose:** what the swarm knows about each channel (geo/topics/hot themes/summary) — the
  context relevance + comments use.
- **Capabilities:** list per-channel profiles (geo layers + label, topics, recent hot themes,
  summary, audience tone, language, sample/post counts, last profiled/themes timestamps). Read-only.
- **API:** `GET /channels/profiles`. (🔒 `POST /channels/internal/{profile,themes}`,
  `GET /channels/internal/profile`.)
- **Data:** `channel_profiles`.
- **Redesign gaps:** read-only — should allow operator override/pinning of geo/topics; no link to
  the channel's posts/decisions/comments; no "re-profile now".

### 1.13 Scouting Radar  🟢 `#/scouting`  ("Радар разведки")  📵-ish
- **Purpose:** viral discoveries ranked by engagement velocity → convert into missions.
- **Capabilities:** searchable/sortable table of scouted targets (platform, author, text,
  velocity heat, engagement, time); dismiss; **convert to mission** (prefills Mission Deck).
- **API:** `GET /scouting/radar`, `POST /scouting/{id}/dismiss`, `POST /scouting/{id}/convert`,
  `POST /scouting/hot-targets`. (🔒 `GET /scouting/internal/sessions`.)
- **Data:** `scouted_targets`, `social_post_targets`.
- **Redesign gaps:** convert flow could open the new mission screen prefilled; no target detail.

### 1.14 Mission Deck  🟢 `#/missions`  ("Миссии")
- **Purpose:** the permanent narrative goals — create, edit, target, staff, run/pause.
- **Capabilities:** create mission (title, goal, stance, explicit side/opponent/arguments/red
  lines, tactic, agent_mode, dynamic_count, targets); per-mission detail with tabs — Overview
  (edit title/goal/stance/position/tactic, delete),
  Targets (add channel/post, approve/reject agent-suggested, delete), Agents (roster + remove,
  **eligible-agents pick-list** with match scores, auto-assign 1α/2β/1γ); pause/resume.
- **API:** `GET /missions`, `GET /missions/{id}`, `POST /missions`, `PUT /missions/{id}`,
  `POST /missions/{id}/status`, `DELETE /missions/{id}`, targets: `POST /missions/{id}/targets`,
  `POST /missions/{id}/targets/{tid}/{approve|reject}`, `DELETE /missions/{id}/targets/{tid}`,
  squad: `POST /missions/{id}/squad`, `DELETE /missions/{id}/squad/{sid}`; target health:
  `POST /missions/internal/target-health` 🔒,
  `GET /missions/{id}/eligible-agents`, `POST /missions/{id}/auto-assign`. (🔒
  `POST /missions/internal/suggest-target`.)
- **Data:** `missions` (also `our_side`, `opponent`, `key_points`, `red_lines`),
  `mission_targets` (also `health`, `health_reason`, `health_checked_at`), `mission_squads`.
- **Redesign gaps:** detail is a drawer → full mission screen wanted; no mission analytics
  (comments posted, reach, target activity); the agent picker is the model to generalize.

### 1.15 Decisions  🟢 `#/decisions`  ("Решения")
- **Purpose:** durable "why the swarm did/didn't react" audit (relevance / skip / comment).
- **Capabilities:** searchable/sortable/paginated history (time, agent, channel, verdict,
  recognized text / reason, post link); filter by kind.
- **API:** `GET /decisions` (filters: kind, limit). (🔒 `POST /decisions/internal/log`.)
- **Data:** `decision_events`.
- **Redesign gaps:** no link from a decision to the agent/channel/mission; no aggregate (skip
  reasons distribution); no time range.

### 1.16 Activity Log  🟢 `#/activity`  ("Журнал (лог)")
- **Purpose:** durable execution logs (comment/reply/react) across workers.
- **Capabilities:** stream of logs (status, time, agent, platform, action, target URL, text);
  filter by agent + platform.
- **API:** `GET /analytics/stream` (agent_id, platform, action_type, since_hours, limit).
  (🔒 `POST /analytics/internal/activity`.)
- **Data:** `agent_activity_logs`.
- **Redesign gaps:** could merge with Decisions + Live Ops into a unified "Activity & Decisions"
  timeline; no per-target thread view.

### 1.17 Devices  🟢 `#/devices`  ("Устройства")  📵
- **Purpose:** virtual device fleet + emulator orchestration (mobile path, telemetry usually
  empty / out of scope).
- **Capabilities:** merged virtual-device + orchestrated-emulator cards; assign agent (**manual
  ID entry — must become a pick-list**); per-device CPU/RAM telemetry; reboot / clear-cache; VNC
  live view; provision / destroy emulators; register into fleet.
- **API:** `GET/POST /souls/devices`, `PUT /souls/devices/{id}/assign`, `DELETE /souls/devices/{id}`;
  `GET /analytics/devices`, `GET /analytics/devices/{id}`, `POST …/{id}/{reboot,clear-cache,launch,
  snapshot/load,snapshot/save,proxy}`, `DELETE …/{id}/proxy`; orchestrator
  `GET /analytics/orchestrator/list`, `POST /analytics/orchestrator/create`,
  `DELETE /analytics/orchestrator/{name}`, `POST /analytics/orchestrator/{name}/stop`.
  (🔒 `POST /souls/internal/device-status`.)
- **Data:** `virtual_devices`.
- **Redesign gaps:** mobile-bound; the assign-agent manual ID input is exactly the pick-list the
  operator called out.

### 1.18 Sandbox Console  🟢 `#/sandbox`  ("Песочница")  📵
- **Purpose:** manually trigger isolated physical typing on a device and watch it live (bypasses
  Redis queues). Mobile.
- **API:** `POST /sandbox/execute`.
- **Redesign gaps:** raw-ish; mobile-bound.

### 1.19 Database Explorer  🟢 `#/database`  ("База данных")
- **Purpose:** direct Postgres table browse + ad-hoc SQL (super-admin).
- **Capabilities:** list live public tables; paginated table view; **inline cell edit** (needs
  `db:edit`); raw SQL console (SELECT). Three-pane layout.
- **API:** `GET /db/tables`, `GET /db/tables/{name}`, `PUT /db/cell`, `POST /db/query`.
- **Redesign gaps (operator-reported BUG):** the data table **does not scroll horizontally** —
  wide tables clip. Also English; the three nested scroll regions are part of the layout problem.

### 1.20 Simulation  🟢 `#/simulation` (+ `#/simulation/<post_id>`)  ("Симуляция")
- **Purpose:** an **isolated Telegram-like polygon** for testing agents/souls, missions, RAG,
  system prompts, comments, reactions and mass generation without touching production. Distinct
  from 1.18 "Песочница" (that one drives a physical device) — nothing here reaches a real channel.
- **Layout:** three columns — activity feed + filters (left), channel posts / single-post thread
  mode (centre), channels + actions + inspector (right).
- **Capabilities:** several isolated worlds (+seed/reset); channel & post CRUD with media,
  reactions, editable time and a full revision history (restore); Telegram-like comment tree
  (reply, edit, change author, react, publish, delete branch); manual accounts vs AI personas
  (editable persona incl. style sliders and **system prompt**, autosaved); simulation-only
  missions grouping agents (run against a polygon post); single and **mass generation**
  (agents + manual accounts together; generate / generate+publish / draft; count, tone, pace,
  order, reply share, prompt override) with live job progress; post/article generation;
  knowledge base + import from production (facts, channel profiles, landscape, souls, missions,
  history); landscape scraping (RSS/Atom, web page, public `t.me/s/` preview — read-only);
  raw-state inspector for any entity.
- **API:** everything under `/simulation/*` (see `SIMULATION.md` §3).
- **Isolation:** own `sim_*` tables on a separate declarative base (no FK into production), own
  RBAC atoms `simulation:{view,manage}`, own Redis queue `queue:sim_gen` (never
  `queue:execution_tasks`), ORPHEUS handler that writes no memory/metrics, and read-only imports.
  Production rate limits, cooldowns and active-hours do **not** apply inside the polygon.
- **Polygon → production:** `GET /simulation/personas/{id}/export` shapes a tested persona as a
  soul draft; the Souls screen has an **«Из симуляции»** picker that prefills the real form.

### 1.21 Legacy / system endpoints  🟢
- `GET/POST/DELETE /agents`, `GET/POST/DELETE /campaigns`, `GET/POST /roles`, `GET /permissions`,
  `GET /health`. (Some predate the mission model; `agents` here = accounts-ish. Audit before
  surfacing in the new UI.)

---

## 2. Cross-cutting capabilities (not a single screen)

- **Genesis engine** — LLM persona synthesis (`genesis_engine.py`).
- **Channel profiler** — strict-JSON per-channel geo/topics/themes (`channel_profiler.py`,
  hybrid cadence).
- **Classifier + embeddings** — LLM categorize + `nomic-embed-text` for knowledge ingest.
- **Mission control reconciler** — 8 s loop: legacy DAG + `reconcile_dynamic_rosters` (auto-fills
  `agent_mode='dynamic'` rosters).
- **Retention policy** — prunes captured events / targets (`retention_policy.py`).
- **Internal sync surface** (🔒) — the swarm's write-back: knowledge ingest, channel profile/themes,
  target suggestions, decision log, device status, activity, capture.

---

## 3. Redesign mandate (operator, Stage 65)

Captured so the redesign is anchored to explicit asks:
1. **Professional command-and-control / monitoring center** — not a set of plain forms; serious,
   information-dense, interactive.
2. **Dashboard** must look serious — trends, queues, throughput, health, drill-through.
3. **Informativeness + interactivity + interaction** are lacking on *every* tab — add detail,
   relationships (agent↔account↔mission↔channel↔decisions), live updates, actions in context.
4. **Editing must be a full dedicated screen** (master → detail route), NOT a modal and NOT a
   side drawer — for accounts, souls, landscape, news hub, knowledge, channel profiles, **and all
   others** — exposing **all** parameters of the selected item.
5. **Pick-from-list everywhere** — every place that asks the operator to type an ID (device→agent,
   etc.) becomes a searchable/filterable/sortable list with detailed item data.
6. **Concrete bug:** Database Explorer table has no **horizontal scroll** — wide tables clip.
7. **Stack:** full migration to a real UI framework — **Mantine 7** adopted (Stage 65: provider +
   theme + `AppShell`; layout/scroll bug fixed). Subsequent stages migrate screens onto Mantine
   (Table/Combobox/Tabs/Card/charts) + a routed master-detail pattern + a reusable EntityPicker.

### Redesign sequence — progress
1. ✅ **Mantine foundation + AppShell** (layout/scroll fixed) — Stage 65.
2. ✅ **Per-entity routing** — `#/<view>/<id>` opens a full-screen detail (Stage 67, `App.tsx`
   `useHashRoute`).
3. ✅ **Reusable primitives** (`src/ui/`, Stage 67): `DataView` (Mantine Table — sticky header,
   horizontal scroll via `Table.ScrollContainer`, sort, search, filter toolbar, row→detail);
   `DetailPage` (full-screen master-detail scaffold: back, header + actions, body, sticky save bar);
   `EntityPicker` (searchable/sortable list modal → pick-from-list, replaces typed IDs); `StatTile`
   (KPI tile + inline SVG sparkline).
4. ✅ **Dashboard v2** (Stage 67) — "Центр управления": readiness alert + 8 KPI tiles (live
   sparklines from rolling poll history) + radar-queue panel; Diagnostics tab kept.
5. **Per-domain migration to full-screen detail** — ✅ **DONE for the core screens** (Stages 67–70):
   `SoulsScreen` (flagship 5-tab editor), `AccountsScreen`, `MissionsScreen` (Обзор/Цели/Агенты +
   eligible-agent `EntityPicker` + create), `LandscapeScreen` (add/edit source), `NewsHubScreen`
   (edit event), `KnowledgeScreen` (facts + inject + fact detail), `ChannelProfilesScreen`
   (read-only profile), `DecisionsScreen` + `ActivityScreen` (DataView timelines). ✅ **Raw-HTML
   rebuilt:** `SoulGenesisView` (Mantine synth form) + `AuthFactory` (Mantine Stepper wizard +
   pick-from-list selects). ✅ **Devices** now uses an `EntityPicker` for device→agent binding
   (no more typed IDs). **Remaining (functional, lower priority):** `DatabaseExplorer` (h-scroll bug
   fixed; full Mantine-Table reskin optional), `SwarmDashboard` (DataTable drill-downs — works),
   `ScoutingRadar` (DataTable — works), `LiveOps` (custom live feed — works), `CloneFactory`
   (Mantine slider + Russified — works), `SandboxConsole` (mobile).
6. **Cross-links & informativeness** — relationship navigation everywhere (account↔soul↔mission↔
   channel↔decisions↔activity). To weave in as each screen migrates.

> **Note:** the old `SoulsContext`/`AccountsManager` (DataTable+SidePanel) are superseded by
> `SoulsScreen`/`AccountsScreen`; their `.css` is still imported in `App.tsx` because it defines
> GLOBAL classes (`.status-badge`, `.tabs`, `.modal-*`, `.header-row`) that un-migrated screens use.
> Drop those imports only once every screen is migrated.
