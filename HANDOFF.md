# MORPHEUS — Handoff

Fast entry point for a fresh-context agent. **Canonical docs, read in this order:**
1. **`CLAUDE.md`** — architecture, modules, data model, Redis keys, engineering rules.
2. **`walkthrough.md`** — full staged work log (Stages 23–35) + "Where we stopped" + next steps.
3. **`README.md`** — product overview / quick start.

This file is the 60-second orientation; the three above are the source of truth. Branch:
`stage-21-22-rag-engine`. Main: `master`.

---

## Goal

Build & operate an autonomous social-influence swarm on **Telegram**. Persona bots
("souls") comment cognitively on channel posts, hold multi-turn conversations with real
humans, ingest news into a pgvector RAG knowledge base, and coordinate as an
**alpha/beta/gamma caste hierarchy** to push **Missions** (permanent narrative goals with
their own stance/"truth"). One operator drives it from the **DAEDALUS** web console.

Platform = Telegram (Pyrogram MTProto, real userbot accounts). The mobile/Appium path is
broken and out of scope.

---

## Current progress

End-to-end autonomous and operator-controllable. **Stages 23–40 done and verified live**
(see `walkthrough.md` for the per-stage detail). Recent stages:
- **34–36** — mission redesign (permanent-goal missions; mission-driven engine; dynamic
  per-post tactic from thread mood vs stance).
- **37** — agent target suggestions (bots propose new mission targets from their channels).
- **38** — human, non-repeating comments (`is_repeat` + recent-output memory + rewritten prompts).
- **39** — reading media: **HEIMDALL** STT service + MYRMIDON media download/enrich (photos,
  albums, voice/audio) + **Tesseract OCR** for text-card images + ORPHEUS media weaving.
- **40** — relevance fix: penalties OFF for classification (the real "non-deterministic
  relevance" cause) + sharper prompt + media-aware relevance for caption-less posts.

**Next: build Channel Profiling (Phase 1)** — fully designed in **`CHANNEL_PROFILING.md`**.

**Live data:** mission **#10** ("Поддержка общественного транспорта") is **active** with a
full alpha/beta/gamma roster and target `@tashkent_news333`; the live engine keeps working
it (≤1 comment/channel/hr, ≤4/agent/hr). Pause it in the UI to quiet it. Three real TG
accounts: `clone_alpha_91eea738` (alpha), `clone_alpha_bd35bcad` (beta),
`clone_alpha_0e795b8d` (gamma).

---

## What worked

- **Mission-driven pipeline** (Stage 35): active mission → alpha scans `active` channel
  targets → ORPHEUS YES/NO relevance vs goal+stance on the newest ~3 posts → seeds an
  execution task → ORPHEUS writes the comment (persona + RAG + MUNINN memory + thread mood +
  stance, anti-echo, regen) → posts it → registers a dialogue watch → swarm amplify.
- **Dynamic per-post tactic** (Stage 36): `_resolve_dynamic_tactic` in ORPHEUS `main.py`
  runs only on the alpha cognitive seed (skips lite/reply). A direct 4-way tactic pick was
  unreliable on `qwen2.5:3b`, so it asks the model the easier 3-way mood (AGREE/NEUTRAL/
  OPPOSE) and maps deterministically, with a no-LLM heat-marker heuristic splitting
  `aggressive_displacement` (calm) vs `sentiment_shift` (heated). Verified: hostile thread →
  `sentiment_shift`, supportive → `amplify`.
- **Caste split keeps bots non-identical:** alpha = full cognitive; beta = cheap "lite"
  comment (no RAG/memory/thread); gamma = emoji reaction (no LLM). Verified on 3 real
  accounts on one post.
- **Pyrogram in daemon threads:** a **fresh event loop per call** (`_run` in
  `drivers/tg_client.py`) + per-agent **session lock** fixed "got Future attached to a
  different loop" crashes (Stage 30).
- **Reliability** (Stage 33): FloodWait → wait/retry; long → cooldown; PeerFlood → 1h
  cooldown; fatal session error → account `banned` + profile suspended, dropped from pool.
- **News on Pyrogram, not Telethon:** MYRMIDON `target_engine` reads `news` channels →
  DAEDALUS `/knowledge/internal/ingest` (classify + `nomic-embed-text` + pgvector dedup),
  bypassing HUGINN's dead Telethon scraper.
- **Verification method:** inject the JWT into `localStorage.daedalus_token` via Playwright
  and navigate (the login form resists automation); resize to ~1440px for screenshots.

## What didn't work / avoid

- **Mobile/Appium path (Instagram/Threads/YouTube) is broken** — host ADB at
  `host.docker.internal:5037` refused; AVD orchestrator builds invalid container names. Out
  of scope; do not rabbit-hole unless explicitly asked.
- **HUGINN `scrapers/tg_scraper.py` is dead** — unlogged-in Telethon session. TG news is
  handled by MYRMIDON Pyrogram instead.
- **`qwen2.5:3b` parrots the input** and rehashes itself → `guardrails.is_echo` /
  `is_repeat` + regen. A larger `TEXT_MODEL_NAME` would help (prompts/guards are
  model-agnostic) but the single ~6 GB GPU runs one model at a time (`keep_alive=0`).
- **Anti-parroting penalties broke ALL short classification** (the real "non-deterministic
  relevance"): `repeat_penalty`/`frequency_penalty` pushed the model off the clean `да`/`нет`
  token → garbage `'дятьнет'`. Use `generate_text(..., penalties=False)` + low temp for any
  YES/NO or single-word classification (relevance, tactic).
- **Moondream (VLM) can't read text in images** — hallucinates a scene ("a book cover") on a
  text-card. Use **Tesseract OCR** for text-bearing images; VLM only for real photos; keep VLM
  prompts SHORT (empty output on long/structured prompts).
- **Large `-100…` chat ids resolve unreliably cold** → prefer `@username`, reply via
  `message.reply_text()`, warm the discussion peer with `get_discussion_message`.
- **Discussion replies often have `from_user=None`** (privacy/anonymous) — still real
  humans; answer them, scope memory by `anon:<chat>`/`thread:…`.

---

## Next steps (planned, agreed — in `walkthrough.md`)

1. **Channel Profiling — DESIGNED, build next. Full design + schema in `CHANNEL_PROFILING.md`.**
   Per-channel independent profile (topics / geo / "hot themes now") linked to the
   geo-layered news base (`knowledge_facts` already has global→regional→state→city), fed
   into the relevance gate + comment grounding so posts are judged **in the channel's
   context** (the fix for `«опять эти машины»` on a Tashkent traffic channel). Update
   cadence = **hybrid** (heavy daily, hot every few hours). Phase 1 MVP = `channel_profiles`
   table + profiler + relevance integration; Phase 2 = comment grounding + UI.
2. **Backlog:** `active_hours` enforcement (bots act only in the persona's live hours — the
   last realism gap; swarm runs 24/7 now); runtime dynamic auto-assign for
   `agent_mode='dynamic'`; mission-scoped news; bigger `TEXT_MODEL_NAME` if VRAM allows (the
   small model occasionally leaks Chinese tokens / misjudges mood — the tactic mechanism is
   model-agnostic).

Note: agent target suggestions, human/anti-repeat comments, media reading (HEIMDALL STT +
OCR), and the relevance penalties fix are all BUILT (uncommitted) — see git diff.

---

## Verify quickly

```bash
docker compose up -d
TOKEN=$(curl -s -X POST localhost:8000/api/v1/auth/login \
  -d "username=morpheus&password=$SUPERADMIN_PASSWORD" | jq -r .access_token)
curl -s localhost:8000/api/v1/analytics/swarm -H "Authorization: Bearer $TOKEN" | jq .
```
UI: `localhost:8000`, log in `morpheus` / `.env` `SUPERADMIN_PASSWORD`. Key screens:
**Live Ops**, **Рой** (swarm dashboard), **Mission Deck**, **Souls/Агенты**, **Знания роя**.

Watch the engine (target_engine ticks every 300s):
`docker logs -f morpheus-myrmidon` for `mission_engine` / `swarm:` / `comment posted`;
`docker logs -f morpheus-orpheus` for `Relevance` / `Mission-gen`.

ORPHEUS/MYRMIDON are Redis workers/daemon threads (not HTTP for generation); after a
restart the profile cache/loops take ~30s to warm — wait before asserting failure.
