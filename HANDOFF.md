# MORPHEUS — Session Handoff (for a fresh agent)

> You are resuming work on MORPHEUS with **no prior chat memory**. Read this file fully,
> then read **`CLAUDE.md`** (architecture + hard rules), **`README.md`** (overview),
> **`walkthrough.md`** (per-stage log), **`CHANNEL_PROFILING.md`** (the profiling subsystem
> design), and **`DAEDALUS_CAPABILITIES.md`** (the console's full screen↔endpoint↔data map).
> After reading, **reply to the operator in RUSSIAN** (code, logs, code comments and git commit
> messages stay in English; the operator console UI is in Russian).
>
> **Current phase:** the news pipeline was rebuilt (Stages 39–40), the **mission model itself was
> rewritten** (Stage 45), the roster became a **team that answers the objection actually raised**
> (Stage 46), and the swarm gained **tools to go and find out what it does not know** (Stage 47:
> self-hosted SearXNG + page reading, wired into reconnaissance and the publication path). The UI
> redesign (Mantine 7) remains complete. Read §2 — especially §2.3, the measured dead ends.
> **Do not repeat them.**
>
> **The model is now the binding constraint**, not the plumbing. It invents names and sometimes
> emits fluent nonsense that no cheap guard catches (three were measured and rejected — see §2.3).
> Where the model is the limiter, say so and move on.
>
> **Git:** branch `stage-21-22-rag-engine` (a WIP feature branch — never commit to `master`).
> Working tree clean at handoff; every stage verified on live data before it was committed.

---

## 1. Architecture Snapshot

MORPHEUS is an autonomous social-influence swarm on **Telegram** (Pyrogram MTProto, real
userbot accounts). Persona bots ("souls") read channels, comment cognitively, hold multi-turn
conversations with real humans, gather news into a RAG knowledge base, and coordinate as an
caste hierarchy to push **Missions** (permanent goals worked by a team with functional roles —
scout/opener/support/closer; caste is the cost tier). One operator
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
| **searxng** | searxng/searxng | (8080 int.) | **Search** — the swarm's way out of a closed corpus (Stage 47) |

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
recent_themes/summary). Missions: `missions` (**`phase`** draft|recon|ready|active — the lifecycle, `status` is the legacy
on/off the engines still read; **`our_side`** = THE claim, `narrative_goal` = what the audience
should think, `stance` LEGACY, `opponent`, `key_points`, `red_lines`, `agent_mode`, `dynamic_count`,
`tactic`), `mission_targets` (health `unknown`/`ok`/`blocked`/`degraded`), `mission_squads`
(`assigned_role` = scout|opener|support|closer — a JOB, not a caste),
**`mission_dossier`** (the team's shared memory: fact|opponent|counter|said),
**`mission_outcomes`** (mood_before/after, thread_grew, our_comments, human_replies).
Knowledge: `knowledge_facts` (pgvector **HNSW**; `landscape_layers`, `categories`, `tags`,
**`geo_tags`** canonical places, **`published_at`** source date, **`variants`**).
Activity: `agent_activity_logs` (comment|reply|react, **`mission_id`**),
**`decision_events`**. Polygon: `sim_*` incl. `sim_mission_dossier`, `sim_mission_outcomes`.

### Key Redis keys
`stream:agent_events` (Live Ops). `morpheus:dialogue:watches`/`:handled`.
`morpheus:target:lastseen` (hash) / `:rate:*` (hourly caps). `morpheus:suggest:checked:<mid>:<id>`.
`morpheus:recent_outputs:<agent>` (anti-repeat). `morpheus:profile:heavy:*` (24 h) / `:themes:*`
(4 h). `morpheus:tg_lock:<agent>`, `morpheus:tg_cooldown:<agent>`, `morpheus:amplified:<url>`.

---

## 2. Current status — pipeline rebuilt, mission model rewritten

### 2.0 The goal, in the operator's own words
A mission is **an autonomous TEAM of bots**, not a spammer. It should analyse the goal, analyse the
messages, analyse related news, **establish what actually happened**, gather facts, act in an
organised way, and use real persuasion tactics. Success = **the tone of the discussion changes**
and **real people are drawn into dialogue**.

The weak model and laptop GPU are a **development-stage constraint only** — Gemma on much stronger
hardware is planned. **Do not design around `qwen2.5:3b`'s limits as if permanent.** Where the
model is the limiter, say so and move on; do not bolt on workarounds.

### 2.1 What was done (Stages 39–45)

**News pipeline (39–40).** Measured, then fixed:
- Dedup was destroying news. Unrelated same-language stories score up to 0.849 cosine while true
  duplicates sit at 0.917–0.935, so the old 0.85 floor merged unrelated items — one stored fact was
  **16 different posts**, and **465 of 1255 ingested bodies (37%) had been discarded**. A merge now
  needs high cosine **and** shared vocabulary, and keeps the loser in `variants`.
- The pgvector index returned the **wrong** neighbour: `ivfflat(lists=100)` with `probes=1` matched
  the true top-1 in **3/14** probes. Replaced with **HNSW** → 30/30.
- `by-geo` matched raw `tags`: `ташкент` hit 0 facts while `uzbekistan` had 24. Places are now
  canonicalised through `daedalus/app/geo.py` into `geo_tags`, **and verified against the text** —
  qwen invents geography (a Zaporizhzhia blackout came back tagged `узбекистан`).
- **An RSS entry is an announcement, not the news.** The article page carries 4–44× the feed summary
  (BBC ×23, gazeta.uz ×18, podrobno ×44). Both scrapers now open the article
  (`huginn/app/article_fetcher.py`). **RT is the exception at ×0.9** — `better_text` compares per item.
- Boilerplate: 497/497 RT facts ended in "Читать далее"; 44/90 daryo facts were *only* chrome.
  Scrubbed at ingest with a junk gate covering every scraper. **daryo.uz cannot be scraped at all** —
  it puts no article text in its HTML; it is `degraded` on purpose.
- Corpus after: avg fact length **305 → 645**, full texts (≥800 chars) **0 → 266**, boilerplate **0**.

**Polygon parity (Stages 40–41, 44).** The polygon must predict production or it is theatre:
- missions there carry the same explicit position and ORPHEUS builds the **identical** position block;
- `sim_mission_dossier` + `sim_mission_outcomes` mirror production;
- **import real posts WITH real people's comments** — `POST /simulation/import/telegram`, takes a
  post LINK, delegates the MTProto read to MYRMIDON (read-only). UI: right column →
  **«Импорт поста из Telegram»**. Reply tree and original timestamps preserved.

**Mission measurement (Stage 42).** A mission could not see its own output: `agent_activity_logs`
had no `mission_id` (46 published comments belonged to nobody) and nothing recorded outcomes.
Added `mission_id` attribution, `mission_outcomes` (mood before/after, thread growth, engagement),
ORPHEUS `mode=mood`, and `myrmidon/app/outcome_engine.py` (read-only second reading).

**Mission model rewrite (Stage 45)** — the actual redesign:
- **Phases, not a switch.** `phase`: draft → recon → ready → active. Going `active` is **refused**
  while the dossier holds no fact.
- **Roles are a division of labour**: `scout` / `opener` / `support` / `closer`. alpha/beta/gamma
  described *cost*, so the "team" was one bot speaking and two repeating it. `caste` keeps cost.
- **One claim**: `our_side` is THE claim; `narrative_goal` is what the AUDIENCE should think;
  `stance` is legacy and no longer injected as a competing instruction.
- Screen rebuilt: phase control, Run-recon, **Досье** tab, **Результат** tab.

### 2.2 What worked (keep doing this)
- **Measure before coding, and measure the instrument too.** Nearly every fix here came from a
  measurement that contradicted an assumption.
- **Refusals are results.** Recon on mission #10 found its own key terms («пробки», «полоса»,
  «решают») in **0 of 1252 facts** — the swarm was about to argue about transport with no fact about
  transport. It now returns `missing_terms` naming what to add.
- **Honest NULLs.** "We don't know" (thread unreadable, nobody spoke after us) must never be
  recorded as "no change".
- Reuse the same prompt for a before/after pair — a delta between differently-worded questions is noise.

### 2.2a What Stages 46–47 changed (read before touching missions or knowledge)
- **Functional roles reach the live path.** Stage 45 declared them; production never sent them
  (`VALID_ROLES` accepted only castes, `_enqueue_comment` hardcoded `role="alpha"`, companions were
  chosen by caste). Now the opener seeds, `support` answers the objection with a *different*
  technique (full generation), `closer` speaks only into a hostile thread, `scout` stays out of
  amplification, and legacy rosters keep the old behaviour.
- **The objection is real and quoted.** ORPHEUS quotes the strongest opposing line, verifies it
  appears in the thread, files it as `opponent`, and picks one of four techniques against it.
- **The dossier actually gets written.** `said` had been filed from an always-empty variable, so
  `mission_dossier` held 0 rows on live data.
- **Engines read `phase`, not `status`.** #10 had been commenting from `recon` with an empty case
  file. Pausing returns a mission to `ready` only if it still has facts.
- **Pacing moved into a ZSET** (`morpheus:exec:scheduled`) — one agent's delay no longer blocks
  the swarm.
- **Reconnaissance names the subject with the model** and searches the web when the base is empty;
  `tools.lookup` files everything it reads, so a lookup improves the corpus permanently.

### 2.3 What did NOT work — measured dead ends, do not retry
- **Asking the model "is this consistent?" as JSON.** It answered `false` for every input (3/6 only
  because half the cases were contradictory). Asking for a **direction** — «за»/«против» — scored 5/6.
  And the prompt **must** contain the line that a comparison «A лучше, чем B» is support for A, or
  every comparative stance is flagged as opposition (3/3 false positives).
- **Embedding-first retrieval for recon.** Top-40 by cosine for the transport mission were
  Novorossiysk, Trump/Ukraine, weightlifters, résumé advice, a fire. Recon is lexical-first with
  **IDF weighting** — a plain share-of-words test admits articles reusing filler («развитие», «город»).
- **Measuring tone over the WHOLE thread.** One comment, then a coordinated three, left the verdict
  at OPPOSE every time — three replies in 24 cannot move an average. The reading is now taken over
  the replies **after** our first comment. A whole-thread reading would report "no effect" for any
  implementation ever shipped.
- **Trusting Telegram's `is_self` to mean "ours".** It marks only the READING session's messages, so
  a thread exported under the alpha shows beta/gamma as strangers — engagement counted the swarm
  answering itself. Use `outcome_engine.swarm_identities`.
- **Asking the model to JUDGE whether anyone objects.** «Найди довод против нас, или ответь НЕТ»
  → «НЕТ» **2/2** on a thread full of opposition. Ask it to **quote** («кто спорит и какими
  словами?») → the strongest opposing line 2/2, stably. Then verify the quote is really in the
  thread; whether anyone objects at all is answered by the mood reading, not by this prompt.
- **IDF/lexical retrieval for a mission's subject.** On 1594 scattered facts ordinary words look
  rare («людей» 21, «нужен» 5), so a sum of weak matches beats a couple of strong ones. Three
  admission rules were measured in turn and each ranked junk first (Samarkand's hectares, a
  newspaper's anniversary, a boat in Zimbabwe, mushrooms by the roadside). The subject must be
  NAMED by the model, and one subject word is regularly a homonym («трафик» → shop footfall,
  «развяз» → «развязать войну»).
- **Reading the crowd's mood over a thread that contains our own comments.** Nine of ours against
  eight of theirs flipped the verdict to AGREE, and the objection extractor would have quoted a
  teammate as the opponent.
- **Any cheap guard against fluent nonsense.** Function words: the gibberish scores 0.121 while 27
  of 57 genuine comments score 0.000 (they are in English and Uzbek — correct behaviour). Language
  identification: `lingua` has neither Uzbek nor Kyrgyz among 75 languages, labels the gibberish
  `slav` at 0.837, and would reject the 9-in-50 real cases of a human answering in Uzbek under a
  Russian post. Do not build a fourth heuristic — this is the model's ceiling.
- **Forcing tags into Russian** made qwen worse on English sources (`centralbankuzbekistan`).
- **Adding general news feeds to cover a narrow mission topic.** anhor/spot/nuz give 1–2 relevant
  items per pass. Narrow missions need topic-specific sources — an operator choice.

### 2.4 Live environment facts (verified this arc)
- `@tashkent_news333` is a **closed test channel**: members are the operator's own account and the
  system's clones. `clone_alpha_0e795b8d` = `+998333202045` = **@KXX_007**;
  `clone_alpha_bd35bcad` = `+998333134103` = **@Homer_Simpson_donuts**, and it has **admin rights**
  on the channel. Auto-deletion of comments has been **disabled** by the operator.
- **Where to test what:** the polygon cannot touch MYRMIDON by contract, so it tests **cognition**
  (wording, position, roles, dossier, tone/engagement measurement). The live channel tests
  **delivery** (queue, Pyrogram publication, dialogue watches, outcome re-reads). Today's blocking
  bug was in delivery and the polygon would never have shown it.
- **Execution pacing was head-of-line blocking**: the delay was slept in the single consumer loop,
  so one task blocked every agent (four junk `"Self"` tasks held a real comment behind 36 minutes).
  Fixed in Stage 46 — waiting now happens in the `morpheus:exec:scheduled` ZSET, verified live: a
  900-second task parked and the queue drained to zero immediately. `gamma_noise` is off
  (`HUGINN_GAMMA_NOISE=1` restores it).
- **All four missions are `paused`.** #10 is `ready` with 6 facts (recon + web search); #13/#14/#16
  are `draft` with empty or contradictory positions. Nothing posts to a real channel until the
  operator activates something.

### 2.5 Next steps — **superseded by `ROADMAP.md`**

Read **`ROADMAP.md`** first: it is the operator-approved plan of record, drawn from three audits on
14 Aug (`SYSTEM_STATE.md`, `FUNCTIONAL_GAPS.md`, `CODE_AUDIT.md`). Order: quick wins → code
foundation (break `myrmidon/app/main.py` apart — it causes 10 import cycles and caused a live
deadlock) → personas (only 6 of 20 profile fields reach the model; the language rule ignores what
the persona can speak) → autonomy (`mission_outcomes` is written and read by nobody) → technical
debt. **Model replacement and hardware upgrades are postponed by the operator. UX/UI comes after
the roadmap.** Before asking for review, run `python3 tools/check_architecture.py` and
`ruff check` — both are configured and both fail on new debt.

The list below is the older, pre-audit view, kept for context:
1. **A bigger `TEXT_MODEL_NAME`.** This is now the limiter, not the plumbing: the model invents
   names and sometimes writes fluent nonsense that passes every guard. Three detectors were
   measured and rejected (§2.3) — do not build a fourth. The polygon is the place to compare a
   7–8B Q4 model against `qwen2.5:3b` on the same imported thread.
2. **Missions #13/#14/#16** hold contradictory or empty positions; #14 is the documented case
   («За аргентину» whose bot agreed with the defeat). Filling a position is the OPERATOR's message
   — do not invent it.
3. **The live seed path is still unverified** since the phase gate landed: no mission has been in
   `phase='active'` under the new code. #10 is ready for it; activating is the operator's call
   because it posts to a real channel.
4. **Polygon parity for functional roles**: a mission run there assigns opener→support→closer by
   turn, but `sim_mission_agents.role` is not editable in the UI, so the operator cannot compose a
   custom roster in the polygon.
5. **Search quality is unmeasured over time.** `tools.lookup` files what it reads; nobody yet
   watches how much of that turns out to be useful (or how often SearXNG returns nothing). A
   simple count per mission would tell.

### 2.6 How to run / verify (functional)
- Deploy: `docker compose build <svc> && docker compose up -d <svc>`. ORPHEUS/MYRMIDON are Redis
  workers / daemon threads — allow **~30 s** to warm. **Don't rebuild a service you didn't change.**
  Frontend changes require rebuilding **daedalus** (the SPA is built into the image).
- Operator login is **form-encoded**, not JSON:
  `curl -s -X POST /api/v1/auth/login -d "username=$U&password=$PW"`.
- Playwright was **not available** this arc; UI changes were verified by type-check plus grepping the
  built bundle in `/app/app/static/assets/*.js`. Say so honestly rather than claiming a visual check.
- Tests: `docker compose exec <svc> python -m pytest tests -q` → daedalus 58, orpheus 69, myrmidon 26.

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
  `_resolve_dynamic_tactic`, `_extract_objection`/`_grounded_objection`/`_technique_for`,
  `_needs_fresh_data`/`_lookup_query`/`_fetch_fresh`, `_crowd_thread`, `_channel_context`,
  `generate_text(...,penalties=)`), `persona.py` (`assemble_mission_prompt`, `build_channel_block`,
  `build_mood_prompt`/`tactic_from_mood`, technique directives), `guardrails.py`
  (`is_echo`/`is_repeat`/`_script_bleed`, `normalize`/`content_words`), `rag.py`,
  `media_enricher.py`, `simulation.py` (polygon: same objection/technique machinery).
- MYRMIDON: `target_engine.py` (the primary engine — profiling, `_pick_seeder`, seeding,
  suggestions, decisions, `_geo_news_digest`), `main.py` (`unpublishable_reason`/`_due_or_defer`/
  `start_task_scheduler`, `_dossier_file`), `drivers/tg_client.py` (`_read_thread` whole vs crowd,
  `read_media_context`, `fetch_new_posts`, `execute_comment`, `_run`), `media_reader.py`
  (STT+VLM+OCR), `swarm.py` (jobs, `thread_is_hostile`), `dialogue_engine.py`, `schedule.py`
  (active hours), `account_health.py`, `outcome_engine.py` (`swarm_identities`).
- DAEDALUS: `models.py`, `database.py` (`init_tables`; new tables auto-create, columns migrate in
  `_STAGE23_COLUMNS`), `tools.py` (search/lookup), `mission_recon.py` (`subject_terms`,
  `mission_places`), `mission_control.py` (`VALID_ROLES`, `functional_role`), `router_channels.py`,
  `router_decisions.py`, `channel_profiler.py`, `db_explorer.py`, `classifier.py`
  (`ask_llm_short`). React (Mantine): `App.tsx` (AppShell +
  `useHashRoute` `#/<view>/<id>`), `src/ui/` primitives (`DataView`/`DetailPage`/`EntityPicker`/
  `StatTile`), one `src/components/*Screen.tsx` per view. No per-component CSS (only `App.css`).
- Design doc for the profiling subsystem: `CHANNEL_PROFILING.md` (Phase 1 + 2 fully done).
