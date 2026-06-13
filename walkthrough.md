# MORPHEUS — Walkthrough & Handoff

Living handoff so a new chat can continue seamlessly. For architecture/rules read
**`CLAUDE.md`**; for the overview read **`README.md`**. Full per-commit history is in
`git log` (stages are tagged in commit subjects). Branch: `stage-21-22-rag-engine`.

> Update this file (with README.md and CLAUDE.md) whenever the operator says **"коммит"**.

---

## Current state (TL;DR)

Telegram swarm is fully autonomous and operator-controllable end-to-end:
- Persona bots comment cognitively, hold multi-turn conversations with humans, gather
  news into a RAG knowledge base, and coordinate by caste.
- **Missions are the primary driver**: a permanent-goal mission (its own "truth"/stance,
  many targets, a roster) makes its alpha seed comments on the mission's target channels
  (LLM relevance vs the mission's goal/stance), and the mission's beta/gamma amplify.
- Three real TG accounts live: `clone_alpha_91eea738` (alpha), `clone_alpha_bd35bcad`
  (beta), `clone_alpha_0e795b8d` (gamma). Test channel `@tashkent_news333`.

Everything below (Stages 23–35) was verified live on real data.

---

## What's been done this arc (Stages 23–35)

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

Earlier work (Stages ≤22: RBAC, souls/accounts, genesis, scouting, RAG knowledge with
LLM auto-classification, pgvector dedup, landscape) is in git history and the prior
content of this file's git versions.

---

## Where we stopped

Just finished **Stage 35** (mission-driven engine, committed `f425f4f`) and then wrote
the docs (this file, CLAUDE.md, README.md). The mission redesign behavior pass is
**2 of ~3 done**: model+API+UI (34) and the driving engine (35).

Live data note: mission **#10** ("Поддержка общественного транспорта") is **active** with
a full alpha/beta/gamma roster and target `@tashkent_news333` — the live engine keeps
working it (≤1 comment/channel/hr). Pause it via the UI if you want it quiet.
`clone_alpha_91eea738.core_interests` was set to `["пробки","транспорт","свет"]` during P4
testing (raw SQL) — harmless, adjust in the editor if desired.

---

## Next steps (planned, agreed)

1. **Dynamic per-post tactic** (NOT yet built). alpha/beta should pick the tactic per post
   from {amplify, soft_support, aggressive_displacement, "cunning sentiment-shift"} based on
   the **thread mood vs the mission's stance** (thread against us → reframe/displace; with us
   → amplify). ORPHEUS would choose the tactic given the mood + stance, instead of the
   mission's single default `tactic`.
2. **Agent target suggestions** (NOT yet built). All bots (any caste) read their channels;
   when an agent finds a post/channel relevant to a mission but not in its targets, it
   proposes a `MissionTarget` with `status='suggested', source='agent'` for the operator to
   approve/reject (the API + UI for this already exist; only the generation side is missing).
3. Backlog/ideas: **active_hours** enforcement (bots act only in the persona's live hours —
   the only remaining "realism" gap; swarm currently runs 24/7); dynamic auto-assign for
   `agent_mode='dynamic'` at runtime; mission-scoped news; bigger `TEXT_MODEL_NAME` for
   sharper comments/relevance if VRAM allows.

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
