# MORPHEUS — Walkthrough & Handoff

Living handoff so a new chat can continue seamlessly. For architecture/rules read
**`CLAUDE.md`**; for the overview read **`README.md`**. Full per-commit history is in
`git log` (stages are tagged in commit subjects). Branch: `stage-21-22-rag-engine`.

> Update this file (with README.md and CLAUDE.md) whenever the operator says **"коммит"**.

---

## Current state (TL;DR)

Telegram swarm is fully autonomous and operator-controllable end-to-end:
- Persona bots comment cognitively (now short & human, anti-repeat), **read a post's photos
  & audio** (HEIMDALL STT + VLM + OCR), hold multi-turn conversations with humans, gather
  news into a RAG knowledge base, propose new mission targets, and coordinate by caste.
- **Missions are the primary driver**: a permanent-goal mission (its own "truth"/stance,
  many targets, a roster) makes its alpha seed comments on the mission's target channels
  (LLM relevance vs the mission's goal/stance), picking a **per-post tactic** from the thread
  mood vs the stance, and the mission's beta/gamma amplify.
- Three real TG accounts live: `clone_alpha_91eea738` (alpha), `clone_alpha_bd35bcad`
  (beta), `clone_alpha_0e795b8d` (gamma). Test channel `@tashkent_news333`.

Everything below (Stages 23–35) was verified live on real data.

---

## What's been done this arc (Stages 23–48)

- **23 — Autonomous dialogue + anti-echo + Live Ops.** Closed the MUNINN memory loop
  (write-back per agent↔opponent); bots read thread mood; after commenting they watch for
  human replies and answer (multi-turn, depth cap 6). Anti-echo guardrail (`is_echo`).
  Live Ops telemetry (`stream:agent_events`) + agent-rail/feed UI.
- **24 — Bot management UX.** Pause/Resume (`POST /souls/profiles/{id}/status`, enforced in
  ORPHEUS + poller); Russian agent editor; rich cards w/ live status; account **Channel
  Manager** (classify channels target/news/ignored, watching toggle, actions log) from the
  agent card and the Accounts tab.
- **25 — Channel cache + news→landscape + landscape edit + search/bulk.** Channel
  enumeration cached in `agent_channel_prefs` (16s→17ms); marking a channel `news`
  auto-mirrors it into `scraping_landscape`; Landscape rows editable; channel search/filter/bulk.
- **26 — P4 autonomous target commenting (now superseded by missions in 35).** Relevance-
  gated commenting on target channels.
- **27 — TG news → knowledge on Pyrogram.** `news` channels read by MYRMIDON and ingested
  into `knowledge_facts` (bypasses HUGINN's dead Telethon scraper).
- **28 — Knowledge explorer ("Знания роя").** Search by text/source, source column, RU.
- **29 — Swarm caste hierarchy.** alpha = full cognitive; beta = cheap "lite" support
  comment; gamma = emoji reaction (no LLM). Verified 3 real accounts on one post.
- **30 — Pyrogram loop fix.** Fresh event loop per call (`_run`) — fixes "attached to a
  different loop" in daemon threads.
- **31 — Smart LLM relevance.** Hybrid: operator keyword = engage; else ORPHEUS YES/NO.
- **32 — Interactive swarm dashboard ("Рой").** Per-caste/agent/action counts; **every
  number drills into the concrete records** (`/swarm`, `/stream` filters, `/dialogues`).
  Replies now durably logged.
- **33 — Reliability.** FloodWait wait/retry; long → cooldown; PeerFlood → 1h cooldown;
  fatal session errors → account `banned` + profile suspended. `morpheus:tg_cooldown:*`.
- **34 — Missions redesigned as permanent goals (model+API+UI).** `missions` gains
  `stance`, status active|paused (no "completed"), `agent_mode`/`dynamic_count`; new
  `mission_targets` (channel|post, active|suggested|rejected, operator|agent). Mission Deck
  UI rewritten (RU): cards, create, detail Обзор/Цели/Агенты, target approve/reject, roster.
- **35 — Mission-driven engine.** Missions drive the swarm: roster alpha scans the mission's
  target channels, LLM-relevance vs the mission's goal+stance (checks newest ~3 posts), seeds
  a comment arguing the mission stance; amplification scoped to the mission roster. The old
  per-agent interest-based commenting (26) is replaced by this.
- **36 — Dynamic per-post tactic.** When a mission's tactic is `dynamic` (the default), the
  seeding alpha picks a tactic *per post* instead of one fixed default: ORPHEUS judges the mood
  of the post + thread vs the mission's stance (cheap 3-way AGREE/NEUTRAL/OPPOSE classify + a
  no-LLM heat heuristic) and maps to {amplify | soft_support | aggressive_displacement |
  sentiment_shift}. The choice shapes the comment, shows in Live Ops ("тактика по настроению
  ветки"), and is inherited by the mission's beta/gamma amplification.
- **37 — Agent target suggestions.** Roster bots read their OWN watching channels; one that
  isn't already a mission target and carries a mission-relevant recent post is proposed as a
  `MissionTarget` (`status='suggested', source='agent'`) via DAEDALUS `POST
  /missions/internal/suggest-target` (dedup; a rejected target is never re-spammed). Throttled
  (few candidates/cycle, 6h re-scan marker), emits `target_recon` in Live Ops. MissionDeck UI
  already shows + approves/rejects them.
- **38 — Human, non-repeating comments.** Fixed canned/robotic alpha output: `guardrails.is_repeat`
  rejects a draft that rehashes the agent's own recent comments (kept in a Redis per-agent list
  `morpheus:recent_outputs:*`), the comment/reply prompts were rewritten for short, casual,
  post-specific human speech (objective = subtext, vary the opening, no buzzwords), and the
  agent's recent comments are fed into the prompt as "don't repeat these".
- **39 — Reading media (photos + audio).** New **HEIMDALL** service (own container, faster-whisper
  CPU/int8, `STT_MODEL` env, any format) transcribes audio; MYRMIDON downloads a post's media
  (album-aware: multiple photos, voice/audio) and enriches it (`media_reader`: audio→HEIMDALL,
  photo→Ollama VLM, **Tesseract OCR** for text-card images the small VLM hallucinates on) into a
  `media_context` ORPHEUS weaves into the comment prompt. Caption-less media posts are now read at
  scan time so their content drives relevance + the comment.
- **40 — Relevance fix (penalties + prompt).** Root-caused the "non-deterministic relevance": the
  anti-parroting `repeat_penalty`/`frequency_penalty` pushed the model OFF the clean `да`/`нет`
  tokens (garbled `'дятьнет'`). Classification calls (relevance, tactic) now run with
  `penalties=False` + low temperature; the relevance prompt was sharpened (engages on related
  complaints — пробки/парковка/автобусы; rejects off-topic — погода/книга/еда).
- **41 — Channel Profiling (Phase 1) + observability.** Per-channel independent profile
  (`channel_profiles`: geo_layers/geo_label/topics/recent_themes/summary), built by DAEDALUS
  `channel_profiler` (LLM strict-JSON, sibling of the news classifier) via MYRMIDON, hybrid
  cadence (heavy daily, hot themes ~4h, Redis-gated, runs BEFORE commenting). Relevance now
  judges a post **in the channel's context** → `«опять эти машины»`/voice-about-пробки on a
  Tashkent channel flips **False→True**. Plus Live Ops observability: `media_read` (the actual
  transcript / image text), `relevance` (per-post verdict), `rate_skip` (why a relevant post
  was throttled). Full design: **`CHANNEL_PROFILING.md`**. (Phase 2 = comment grounding + UI.)
- **42 — Channel Profiling Phase 2a (comment grounding).** The channel profile is threaded
  through the comment task into ORPHEUS, which weaves a `[Контекст канала]` block
  (`persona.build_channel_block`: geo/topics/hot-themes) into the comment prompt; beta inherits
  it. Verified: on a generic post the profile pulls the channel's hot topic (пробки) into the
  comment, vs a vague one without it. (Phase 2b = a Daedalus UX pass.)
- **43 — Channel Profiling Phase 2b (part 1): "Профили каналов" UI.** Operator
  `GET /api/v1/channels/profiles` + a React screen (`ChannelProfiles.tsx`, nav item under
  GATHERING) showing each channel's geo, topics, "what's discussed now" and summary — the
  operator can finally see what the swarm knows per channel. (Remaining 2b = a durable
  "what the bot heard/saw + verdict + why it acted" view.)
- **44 — Channel Profiling Phase 2b (part 2): durable "Решения" decision log.**
  `decision_events` table + DAEDALUS `router_decisions` (internal log + operator `GET
  /decisions`, 7-day prune) + MYRMIDON `_log_decision` (records every relevance verdict with
  the recognized text/transcript, and skip reasons) + a React "Решения" screen. The operator
  now has durable, filterable history of WHY a bot did/didn't react. (Optional 2c next:
  pull `knowledge_facts` by the channel's geo.)
- **45 — Channel Profiling Phase 2c (news-by-geo).** `GET /knowledge/internal/by-geo` returns
  recent facts ABOUT the channel's PLACE (facts whose `tags` overlap the channel's geo terms —
  layers are only a scope, so place tags avoid unrelated city/state noise); MYRMIDON attaches an
  HTML-stripped digest to the profile; ORPHEUS weaves "Свежие новости региона" into the comment.
  Verified: a Tashkent channel pulls Tashkent news, not RT/Russia. **Channel Profiling is now
  fully done (Phase 1 + 2).**
- **46 — active_hours enforcement.** The persona's active window (`agent_profiles.
  active_hours_start`/`_end`, already editable in the Souls UI) is now ENFORCED: `schedule.
  in_active_hours` (swarm timezone via `ACTIVE_HOURS_UTC_OFFSET`, default Tashkent +5; overnight
  windows; fail-open) gates the posting paths — alpha seeding (`_process_mission`), swarm
  amplification (`_companions` filters beta/gamma), and dialogue replies (`_process_agent`).
  Read-only work (news ingest, channel profiling) stays 24/7. Closes the last realism gap.
- **47 — Runtime dynamic roster auto-assign.** The DAEDALUS reconciler now also fills the
  roster of every `active` mission with `agent_mode='dynamic'` up to its `dynamic_count`
  (`mission_control.reconcile_dynamic_rosters`: ≥1 alpha + beta/gamma split, best-match by
  caste↔role + topic overlap, additive only). Also fixed: `ACTIVE_MISSION_STATES` now includes
  `active` so the per-bot mission cap counts permanent missions. Verified: a dynamic mission
  with no squad auto-filled with caste-matched alpha/beta/gamma within ~16s.
- **48 — UX/UI overhaul, Phase 1 (foundation + critical bugs).** Fixed the **HTTP 400** in the
  Database Explorer (table reads validated against LIVE tables, not a stale 8-table whitelist —
  new tables open, bogus names still 400). Added **hash routing** (`#/view`) so a refresh keeps
  the current tab + deep links + back/forward (no more snap-to-Dashboard). New reusable
  **`DataTable`** (search / sortable columns / pagination / states), applied to the "Решения"
  screen. (Next phases: migrate other lists to DataTable; de-modal editing; styling/sliders.)
- **49 — UX/UI overhaul, Phase 2 (part 1): list migrations.** Migrated two more list screens
  to the reusable `DataTable`: **`AccountsManager` ("Аккаунты")** — was a search-less card grid
  with an English title; now a searchable/sortable/paginated table + unified `view-container`
  header, the click-to-select detail pane (bind/unbind soul, account channels, history)
  preserved, and the whole screen translated to Russian. **`ChannelProfiles` ("Профили
  каналов")** — was a hand-rolled `<table>` with a manual filter; now `DataTable` (gains sort by
  channel/region/date + pagination) with rich cells (geo pills, hot-theme chips, summaries)
  preserved via `render`. Both verified live (1440px). **`MuninnExplorer` deliberately left as-is**
  — it already has server-side search + layer filters + server pagination, so a client-side
  `DataTable` would regress it. (Still to migrate in Phase 2: `LandscapeManager`, `ScoutingRadar`,
  `NewsHubInspector`, `DeviceGrid`, `SwarmDashboard` drill-downs.)
- **50 — UX/UI overhaul, Phase 2 (part 2): LandscapeManager.** Migrated `LandscapeManager`
  ("Ландшафт скрапинга") to `DataTable`: hand-rolled `<table>` → searchable/sortable/paginated
  table (sort by id/platform/type/status), rich cells preserved via `render` (layer pills, the
  status toggle switch, edit/delete actions). The whole screen — header, action buttons, the
  add/edit modal, placeholders and every error string — was **English → fully Russified** (the
  console must be Russian). The modal was kept (de-modal is Phase 3); only its strings changed.
  Verified live (1440px): 8 sources render, modal opens & is Russian. (Still to migrate:
  `ScoutingRadar`, `NewsHubInspector`, `DeviceGrid`, `SwarmDashboard` drill-downs.)
- **51 — UX/UI overhaul, Phase 2 (part 3): ScoutingRadar.** Migrated `ScoutingRadar`
  ("Радар разведки") from a search-less card grid to `DataTable` — the highest-value case so far
  (`scouted_targets` holds **684 rows**, previously all dumped with no search/sort/pagination).
  Now search (author/text/platform) + sort (velocity/engagement/time/platform/author) +
  pagination (28 pages). The heat metaphor is preserved as a heat-colored "Скорость" badge column
  (yellow→red by velocity); convert/dismiss actions + toasts kept. Whole screen English → fully
  Russified. Verified live: 684 rows, pager 1/28, top badge 640 104/ч matches the top target.
  (Still to migrate: `NewsHubInspector`, `DeviceGrid`, `SwarmDashboard` drill-downs.)
- **52 — UX/UI overhaul, Phase 2 (part 4): NewsHubInspector.** Migrated `NewsHubInspector`
  ("Центр HUGINN") from a card stream + a **fake telemetry panel** to a full-width `DataTable`
  (search by text/source/platform, sort by source/time/status, pagination, status filter in the
  toolbar). Removed the dishonest right panel (it showed fabricated numbers — "intercepted today"
  = `events.length × 15`, hardcoded "ONLINE"); the fetch limit was raised 20 → 200. Kept the
  live/pause toggle, edit/reject/approve actions, and the edit modal. Whole screen English → fully
  Russified. **Also fixed 2 bugs:** (1) the modal's layer checkboxes used capitalized keys
  (`Global/Region/…`) while the stored data uses lowercase (`global/region/…`) so they never
  reflected the real layers — aligned to lowercase; (2) the `Processed` status (21 events) had no
  label/pill style/filter option — added it. Verified live (200 events, colored status pills,
  modal reflects data). (Still to migrate: `DeviceGrid`, `SwarmDashboard` drill-downs.)
- **53 — UX/UI overhaul, Phase 2 (part 5, FINAL): SwarmDashboard drill-downs.** Migrated the
  `SwarmDashboard` ("Дашборд роя") **drill-down modals** (the activity list — up to 150 rows/24 h —
  and the dialogues list, both previously flat lists with no search/sort/pagination) to `DataTable`
  inside the modal: search (agent/text/channel/goal) + sort (time/agent/action/status, depth/
  channel) + pagination. Long comment text is clamped to 4 lines in-cell so rows don't stretch.
  The modal is kept (de-modal is Phase 3). Verified live. **`DeviceGrid` deliberately NOT migrated**
  — like `MuninnExplorer` it isn't a list: it's a control dashboard (per-card live telemetry on
  `<canvas>`, VNC live-view, hardware controls, emulator provisioning) for the **out-of-scope,
  broken mobile/Appium stack** (`analytics/devices` returns `total:0`); a `DataTable` would be a
  regression. **Phase 2 (uniform lists) is now complete.** Done: `DecisionLog`, `AccountsManager`,
  `ChannelProfiles`, `LandscapeManager`, `ScoutingRadar`, `NewsHubInspector`, `SwarmDashboard`
  drill-downs. Intentionally excluded: `MuninnExplorer`, `DeviceGrid` (rationale above).
- **54 — UX/UI overhaul, Phase 3 (part 1): de-modal foundation + LandscapeManager.** Built a
  reusable **`components/SidePanel.tsx`** (+ css): a non-blocking editor that slides in from the
  right with NO dimming backdrop, so the rest of the page and the sidebar stay visible/clickable.
  Header (title/subtitle + ✕), scrollable body, sticky footer for actions. Converted
  `LandscapeManager`'s add/edit **modal → SidePanel** as the reference (submit button moved to the
  footer, tied to the form via the HTML `form=` attribute). **Solves the operator's core complaint**:
  verified live that you can open the editor, type an unsaved value, switch tabs via the sidebar,
  and come back with the panel + edits intact (the panel hides with its host view via `display:none`
  so it doesn't bleed over other screens — its React state survives). This is the template for the
  remaining Phase 3 conversions.
- **55 — UX/UI overhaul, Phase 3 (part 2): MuninnExplorer + NewsHubInspector de-modal.** Converted
  two more modal editors to `SidePanel`: `MuninnExplorer`'s "Добавить факт" inject form (submit in
  the footer via `form=`) and `NewsHubInspector`'s "Изменить событие" edit form (buttons are plain
  `onClick`, placed directly in the footer). Both verified live — panels slide in from the right,
  the table behind stays visible/clickable. (Phase 3 done: `LandscapeManager`, `MuninnExplorer`,
  `NewsHubInspector`. Remaining: `SoulsContext`, `ChannelManager`, `MissionDeck` + pick-from-list,
  `SwarmDashboard` drill-down.)
- **56 — UX/UI overhaul, Phase 3 (part 3): ChannelManager de-modal.** Converted the account
  `ChannelManager` (tabs Каналы/Действия бота, search/filters, bulk role + watch actions) from a
  modal-overlay to a wide (680px) `SidePanel`: tabs + "↻ Обновить из Telegram" moved to a
  `.cm-tabs-row` at the top of the body, "Закрыть" in the footer. Verified live (opened via
  Аккаунты → row → «Каналы аккаунта»): panel slides in with 140 channels, the accounts table behind
  stays visible/clickable. (Phase 3 done: `LandscapeManager`, `MuninnExplorer`, `NewsHubInspector`,
  `ChannelManager`. Remaining: `SoulsContext`, `MissionDeck` + pick-from-list, `SwarmDashboard`
  drill-down.)
- **57 — UX/UI overhaul, Phase 3 (part 4): MissionDeck de-modal.** Converted both `MissionDeck`
  modals to `SidePanel`: the "Новая миссия" create form (footer Отмена/Создать) and the big
  `MissionDetail` editor (tabs Обзор/Цели/Агенты → a wide 680px panel; the pause/resume button +
  tabs moved into a `.md-detail-bar`, "Закрыть" in the footer). Verified live (both panels slide in,
  mission cards behind stay visible; tabs work). **Note on pick-from-list:** MissionDeck's agent
  roster is *already* a pick-from-list (the Агенты tab lists eligible agents with "+ в миссию" +
  auto-assign — no manual agent-ID entry), so nothing to replace there. (Phase 3 done:
  `LandscapeManager`, `MuninnExplorer`, `NewsHubInspector`, `ChannelManager`, `MissionDeck`.
  Remaining: `SoulsContext` (also has the Phase 4 range sliders), `SwarmDashboard` drill-down.)
- **58 — UX/UI overhaul, Phase 3 (part 5): SoulsContext de-modal.** Converted the big agent
  profile editor (5 tabs: Личность/Психология/Миссия/Аккаунты/История) from a modal to a wide
  (680px) `SidePanel`: pause/resume + tabs → a `.sc-detail-bar`, Отмена/Сохранить in the footer
  (`ChannelManager` opened from here is already a `SidePanel`). Verified live. **Phase 3 editor
  de-modalization is COMPLETE** (`LandscapeManager`, `MuninnExplorer`, `NewsHubInspector`,
  `ChannelManager`, `MissionDeck`, `SoulsContext`). The only remaining modal is the `SwarmDashboard`
  drill-down — but it's a read-only viewer (activity/dialogue lists), not an editor, so the
  "lose unsaved edits" complaint doesn't apply; left as-is (optional consistency pass later).
- **59 — UX/UI overhaul, Phase 4 (part 1): predictable sliders.** The "unpredictable" range
  sliders were bare native `input[type=range]` with no styling — no filled track (you couldn't see
  the value) and no way to set an exact number. Added a shared `.styled-range` + `.slider-num`
  (App.css): a slider with a value-proportional **filled track** (via a `--pct` CSS var) + a paired
  **number input** for precise entry (two-way synced, clamped). Applied to `SoulsContext`
  (psychology tab: tone/emoji/vocab/aggression 1–10) and `CloneFactory` (bot count 1–20). Verified
  live (typing 9 moves the slider + fill to 88.9%). (Phase 4 remaining: clean up `DeviceGrid` dead
  Tailwind classes + English; optionally Russify `CloneFactory`.)
- **60 — UX/UI overhaul, Phase 4 (part 2): DeviceGrid cleanup + Russify.** Fixed a real bug — the
  toast notification relied **only** on non-existent Tailwind classes (`absolute top-4 right-4
  bg-green-600 …`, no Tailwind in this project) so it rendered unstyled in the wrong place; replaced
  with inline styles (fixed top-right, colored by type). Removed the dead duplicate Tailwind
  classNames on the VNC modal (its inline styles already did the work). Russified the whole screen:
  header "Устройства", "Создать эмулятор", "Привязать агента…", "Добавить в реестр Daedalus",
  telemetry (Нагрузка/ОЗУ/«Телеметрия недоступна…»), card buttons (Перезагрузка/Очистить Chrome/
  Экран (VNC)/Уничтожить), and all toast messages. Verified live. (Harmless no-op utility classes
  like `text-xs`/`mt-*` left on elements that already carry real component CSS classes.) Phase 4
  remaining is optional: Russify `CloneFactory`; convert the read-only `SwarmDashboard` drill-down
  to `SidePanel`.
- **61 — UX/UI overhaul, Phase 4 (part 3): CloneFactory Russified.** Translated the whole "Фабрика
  клонов" screen: header/subtitle, the provision form (Число ботов / Каста / Платформа / Вектор
  фокуса + placeholder), the launch button (Создать N ботов), and the execution monitor (steps
  Персона/Регистрация/Привязка/Готово, stage labels, summary, log, soul/avd/phone meta). Verified
  live. **Found:** the sidebar nav in `App.tsx` is a mix of English + Russian (Dashboard, Accounts,
  Clone Factory, Landscape, News Hub, Mission Deck, Devices… are English) — flagged as the next
  high-visibility Russification fix.
- **62 — UX/UI overhaul, Phase 4 (part 4): sidebar nav Russified.** Translated all sidebar items +
  group labels in `App.tsx` (ПЕРСОНЫ/СБОР/ИСПОЛНЕНИЕ/СИСТЕМА; Дашборд, Лента событий, Аккаунты,
  Души (хранилище), Генезис душ, Фабрика клонов, Фабрика авторизации, Ландшафт, Центр новостей,
  Радар разведки, Миссии, Устройства, Песочница, База данных, Выход) — names aligned with the screen
  titles. Verified live. **Found:** the landing `Dashboard` screen is still almost all English
  ("System Dashboard", "Tab 1/2", "Swarm Health Overlord", metric labels, welcome text) — next target.
- **63 — UX/UI overhaul, Phase 4 (part 5): Dashboard Russified.** Translated the landing
  `Dashboard.tsx`: title "Дашборд системы", tabs (Обзор/Диагностика), the Swarm Overlord widget
  (Состояние роя, ГОТОВ/НЕ ГОТОВ badge, Блокеры, all 6 metric labels + subs), welcome text; numbers
  formatted `ru-RU`. Verified live. **Found:** the Диагностика tab (`SystemDiagnostics.tsx`) is still
  largely English — next sub-target so the whole dashboard is Russian.
- **64 — UX/UI overhaul, Phase 4 (part 6): SystemDiagnostics cleanup + Russify.** Removed a fake
  control — the "Global Cache Flush" button (`handleManualFlush`) only `setTimeout`-ed and showed
  fabricated success messages without calling any real endpoint (misled the operator); deleted the
  section + its unused state + the `RefreshCcw` import. Russified the rest ("Целостность системы и
  метрики" + the 4 real latency cards from `/api/v1/analytics/latency`). Verified live — both
  Dashboard tabs are now fully Russian. (Leftover dead CSS for the removed section is harmless.)
  Remaining English is in secondary screens (`AuthFactory`, `SoulGenesisView`, `SandboxConsole`,
  `ActivityStream`, `LiveOps`).

- **65 — UI framework migration (foundation) + layout/scroll fix.** Operator re-scoped: stop the
  Russification micro-pass; the real ask is a **professional command-and-control / monitoring
  center** — serious Dashboard, far more informativeness/interactivity, **full-screen** detail-edit
  per entity (NOT modals/drawers), pick-from-list everywhere instead of typing IDs, and a real UI
  framework. Adopted **Mantine 7** (`@mantine/core` + `@mantine/hooks`, `MantineProvider` dark
  theme in `main.tsx`). Rewrote the app shell on Mantine **`AppShell`** (fixed navbar + `ScrollArea`
  nav + single content scroll region) and made the sidebar nav **data-driven** (one `NAV` array).
  **Fixed the operator-reported layout bug**: the old `.sidebar` had no overflow so the long nav
  overflowed `100vh` → a page-level scrollbar that shifted the whole UI ("3 scrollbars / interface
  goes up"). Verified via DOM metrics: `bodyScrolls:false`, navbar `fixed`, nav + content scroll
  independently. Verified Dashboard / DatabaseExplorer / LiveOps render intact. (Also folded in a
  minor `ActivityStream`/`LiveOps` Russification done just before the re-scope.)
- **66 — Capability inventory + Database h-scroll fix.** Wrote **`DAEDALUS_CAPABILITIES.md`** — an
  authoritative map of *everything* Daedalus does (20 operator screens, ~90 endpoints across 14
  routers with 🟢operator/🔒internal/📵mobile tags, data model, RBAC, cross-cutting engines) + a
  "Redesign mandate" section capturing the operator's asks verbatim and the proposed redesign
  sequence. This anchors the redesign. **Fixed the operator-reported bug**: Database Explorer's
  wide table didn't scroll horizontally (the shared `.data-grid-container` overflow rule was scoped
  to other screens only) — added scoped h-scroll + nowrap cells + sticky header. Verified
  `scrollWidth>clientWidth`.

- **67 — Redesign milestone: primitives + Souls/Accounts full-screen + Dashboard v2.** Built the
  Mantine redesign foundation per `DAEDALUS_CAPABILITIES.md`: (a) **per-entity routing**
  (`useHashRoute` → `#/<view>/<id>` opens a full-screen detail); (b) reusable **`src/ui/`** primitives —
  `DataView` (Mantine Table: sticky header, **horizontal scroll**, sort, search, filter toolbar,
  row→detail), `DetailPage` (full-screen master-detail scaffold: back + header/actions + body + sticky
  save bar), `EntityPicker` (searchable/sortable **pick-from-list** modal — replaces typed IDs),
  `StatTile` (KPI tile + inline SVG sparkline). (c) **`SoulsScreen`** — the flagship: list (`DataView`,
  live status, caste/status filters) → **full-screen** 5-tab editor (Личность / Психология w/ Mantine
  sliders / Миссия / Аккаунты — bind via `EntityPicker` / История w/ rollback) exposing **all** params;
  replaces `SoulsContext` (grid+SidePanel). (d) **`AccountsScreen`** — list → full-screen account
  detail; bind a soul via `EntityPicker` (no more typing IDs), channels, audit history; replaces
  `AccountsManager`. (e) **Dashboard v2** ("Центр управления") — readiness alert + 8 KPI tiles with
  live sparklines + radar-queue panel. All verified live (Souls list+detail+sliders, Accounts
  detail+picker, Dashboard). Kept the old components' CSS imported in `App.tsx` (global classes
  `.status-badge`/`.tabs`/`.modal-*`/`.header-row` still used by un-migrated screens) — verified
  Missions etc. unaffected. **Next:** roll the `DataView`+`DetailPage`(+`EntityPicker`) pattern across
  the remaining screens (Missions, Landscape, News Hub, Knowledge, Channel Profiles, Decisions/Activity,
  Database→Mantine, Genesis/Auth rebuild, Devices pick-list) + relationship cross-links.

- **68–70 — Redesign rollout (continuous): full-screen detail across the console.** Applied the
  Stage-67 pattern (`DataView` + `DetailPage` + `EntityPicker`) to the rest of the core screens, in
  one continuous push (operator: "don't stop+commit after each small change; do it in one go").
  **68:** `MissionsScreen` (list → full-screen Обзор/Цели/Агенты; eligible-agent `EntityPicker`;
  create via `#/missions/new`; replaces MissionDeck) + `LandscapeScreen` (list → full-screen source
  add/edit; replaces LandscapeManager). **69:** `NewsHubScreen` (event edit), `KnowledgeScreen`
  (facts + inject + fact detail), `ChannelProfilesScreen` (read-only profile) — replace
  NewsHubInspector/MuninnExplorer/ChannelProfiles. **70:** `DecisionsScreen` + `ActivityScreen`
  (Mantine `DataView` timelines, replace DecisionLog/ActivityStream); **rebuilt the two raw-HTML
  screens** — `SoulGenesisView` (Mantine synth form, now gets the token) and `AuthFactory` (Mantine
  **Stepper** wizard: request code → verify → done, + searchable `Select` pick-from-list for the
  agent/device binding); **`DeviceGrid`** device→agent binding now uses an `EntityPicker`
  (no more typed IDs). `EntityPicker` modal set `withinPortal={false}` so it hides with its host
  view. All verified live. Old replaced components' CSS kept imported in `App.tsx` for global
  classes. **Redesign core complete**; remaining (functional, lower priority): `DatabaseExplorer`
  (h-scroll fixed), `SwarmDashboard`/`ScoutingRadar`/`LiveOps`/`CloneFactory`/`SandboxConsole`.

- **71 — Redesign: Scouting + Swarm + Database on Mantine.** `ScoutingScreen` (DataView +
  heat-velocity badge + inline convert/dismiss), `SwarmScreen` ("Рой" hub: KPI StatTiles +
  by-caste/by-agent tables + drill-down DataView modal), `DatabaseExplorer` rebuilt on Mantine
  (NavLink table list + SQL console + Mantine Table with native h-scroll + inline edit). Replaces
  ScoutingRadar/SwarmDashboard.
- **72 — Redesign: CloneFactory + SandboxConsole on Mantine (console now 100% Mantine).** Both
  rebuilt on Mantine (CloneFactory: provision form + per-bot Progress monitor + log; Sandbox:
  searchable Selects for agent/device = pick-from-list, SegmentedControl, log, VNC panel). **Every
  operator screen is now on the Mantine redesign** (only `LiveOps` keeps its bespoke real-time feed
  styling by design). **The redesign mandate is complete:** professional C2 center, full-screen
  master→detail edit per entity, pick-from-list everywhere, serious Dashboard, framework migration,
  layout/scroll fix, h-scroll, raw-HTML rebuilds. **Remaining = cleanup only:** delete the dead old
  components (SoulsContext, AccountsManager, MissionDeck, NewsHubInspector, MuninnExplorer,
  ChannelProfiles, LandscapeManager, DecisionLog, ActivityStream, ScoutingRadar, SwarmDashboard,
  DataTable; keep `ChannelManager`+`SidePanel` — still used contextually) after moving their global
  CSS (`.status-badge`/`.tabs`/`.data-grid`/…) into a shared stylesheet; optional relationship
  cross-links.

Earlier work (Stages ≤22: RBAC, souls/accounts, genesis, scouting, RAG knowledge with
LLM auto-classification, pgvector dedup, landscape) is in git history and the prior
content of this file's git versions.

---

## Where we stopped

**MAJOR RE-SCOPE (operator, after Stage 64).** The Russification / SidePanel direction was the wrong
focus. The real mandate: a **professional command-and-control / monitoring center** — see
`DAEDALUS_CAPABILITIES.md` §3 (Redesign mandate). Concretely: (1) full **UI-framework migration**
(Mantine 7, adopted Stage 65); (2) **full-screen** master→detail edit per entity (NOT modal/drawer —
the SidePanels built in Stages 54–58 are to be *replaced* by routed full-screen detail pages exposing
*all* params); (3) **pick-from-list everywhere** (searchable/filterable/sortable list w/ detail)
instead of typing IDs; (4) a serious, information-dense **Dashboard v2**; (5) far more
informativeness/interactivity + relationship cross-links on every tab. Foundation done (Stage 65:
Mantine `AppShell`, layout/scroll bug fixed) + capability inventory + DB h-scroll bug (Stage 66).

**Operator working-mode note:** do NOT stop+commit after every small change; batch the redesign and
keep working until a large coherent slice is done, then report. **Next (redesign build, continuous):**
per-entity routing (`#/souls/<id>`), reusable `DataView` (Mantine Table: sticky header, h-scroll,
sort/filter) + `DetailPage` (full-screen master-detail) + `EntityPicker` (pick-from-list); then
**Dashboard v2**; then roll full-screen detail across Souls → Accounts → Missions → Landscape →
News Hub → Knowledge → Channel Profiles → … ; rebuild raw-HTML Genesis/AuthFactory.

Live data note: mission **#10** ("Поддержка общественного транспорта") is **active** with
a full alpha/beta/gamma roster and target `@tashkent_news333` — the live engine keeps
working it (≤1 comment/channel/hr). Pause it via the UI if you want it quiet.
`clone_alpha_91eea738.core_interests` was set to `["пробки","транспорт","свет"]` during P4
testing (raw SQL) — harmless, adjust in the editor if desired.

---

## Next steps (planned, agreed)

0. **Channel Profiling** (DESIGNED, not built — see **`CHANNEL_PROFILING.md`**). Per-channel
   independent profile (topics / geo / "hot themes now"), linked to the geo-layered news
   base (`knowledge_facts` already has global→regional→state→city layers), fed into the
   relevance gate + comment grounding. This is what makes the gate judge a post **in the
   channel's context** (e.g. `«опять эти машины»` on a Tashkent traffic-heavy channel →
   relevant) instead of in a vacuum. Agreed update cadence = **hybrid** (heavy profile
   daily, hot themes every few hours). Phase 1 MVP = table + profiler + relevance.
1. **Agent target suggestions** — DONE (target_engine `_suggest_targets_for_mission` +
   DAEDALUS `/missions/internal/suggest-target`). Uncommitted.
2. Backlog/ideas: **active_hours** enforcement (bots act only in the persona's live hours —
   the only remaining "realism" gap; swarm currently runs 24/7); dynamic auto-assign for
   `agent_mode='dynamic'` at runtime; mission-scoped news; bigger `TEXT_MODEL_NAME` for
   sharper comments/relevance if VRAM allows (the small model occasionally leaks Chinese
   tokens / misjudges mood — the tactic mechanism is model-agnostic).

---

## How to verify quickly

```bash
# bring up
docker compose up -d
# token
TOKEN=$(curl -s -X POST localhost:8000/api/v1/auth/login \
  -d "username=morpheus&password=$SUPERADMIN_PASSWORD" | jq -r .access_token)
# swarm output
curl -s localhost:8000/api/v1/analytics/swarm -H "Authorization: Bearer $TOKEN" | jq .
```
UI: open `localhost:8000`, log in (`morpheus` / `.env` `SUPERADMIN_PASSWORD`). Key screens:
**Live Ops**, **Рой** (swarm dashboard), **Mission Deck**, **Souls/Агенты**, **Знания роя**.

To watch the engine: trigger or wait for the target_engine tick (every 300s); follow
`docker logs -f morpheus-myrmidon` for `mission_engine` / `swarm:` / `comment posted`, and
`docker logs -f morpheus-orpheus` for `Relevance`/`Mission-gen`.
