# MORPHEUS — Channel Profiling

> **Status: PHASE 1 BUILT & VERIFIED (Stage 41).** Phase 2 (comment grounding + UI) and
> Phase 3 (other platforms) remain. Update cadence = **hybrid** (heavy profile rarely,
> "hot themes" often). Companion docs: `CLAUDE.md`, `walkthrough.md`, `HANDOFF.md`.
>
> **Phase 1 (done):** `channel_profiles` table (`models.py`); DAEDALUS `channel_profiler.py`
> (LLM strict-JSON geo/topics/summary + hot themes) + `router_channels.py` (3 internal
> endpoints); MYRMIDON `target_engine` profiler (hybrid cadence, Redis-gated, runs BEFORE
> commenting) + relevance now judges a post IN the channel's profile context. Verified:
> `@tashkent_news333` → geo "ташкент, узбекистан" `['state','city']`, topics
> пробки/дороги/свет, hot themes пробки/…; relevance on `«опять эти машины»`
> **False→True** with the profile (off-topic weather/book stay/→ False).

---

## 1. Why (the problem)

Relevance currently judges a post **in a vacuum**. A real case: a user posted
`«опять эти машины. ну когда это закончится?»` (a text-card image) to the Tashkent
city channel `@tashkent_news333`, where traffic ("пробки") is a recurring topic. To a
human this is obviously a traffic complaint → relevant to a *public-transport* mission.
The `qwen2.5:3b` gate, seeing only the bare phrase, says НЕТ — it can't connect "these
cars" → traffic → public transport without **context**.

The operator's point: *we already ingest news from BBC/CNN/Fox/RT down to regional
publics — not for fun.* That world→region→country→city knowledge, plus each channel's
own character, should make the swarm judge and comment **in context**, per channel.

The three things the system should know about each channel (operator's words):
1. **What topics is the channel about?**
2. **What is being discussed lately** (in the world / region / country / city / this
   channel)?
3. **Where is it** — e.g. `@tashkent_news333` is a *Tashkent* channel; the system
   should infer this from the name + posts and link it to the geo-scoped news base.

This must work per channel **independently**, and (later) across platforms (Telegram
now; Instagram / Threads / Twitter / YouTube when their drivers exist).

---

## 2. The foundation already exists

`knowledge_facts` (pgvector RAG, see `models.py`) is **already geo-layered + themed**:
- `landscape_layers` — closed set **`global → regional → state → city`** (a fact can
  span several, e.g. a city event of national importance = `["state","city"]`).
- `categories` — themes (politics, economy, infrastructure, …).
- `tags` — salient entities/keywords. Plus `embedding`, source clustering, `timestamp`.

News ingestion already LLM-classifies into this structure (`classifier.auto_classify_text`,
strict-JSON). ORPHEUS already retrieves by layer + similarity (`rag.fetch_fresh_context`).

So the **world→region→country→city hierarchy is already in the data.** Channel Profiling
is mainly about (a) characterizing each channel into the *same* geo/theme vocabulary and
(b) feeding that context into relevance + comment generation.

---

## 3. Data model — `channel_profiles`

One row **per channel** (NOT per agent — a profile is shared/independent of who reads
it; many agents may feed the same channel's profile). Platform-agnostic.

```
channel_profiles
  id              PK
  platform        VARCHAR(30)    -- 'telegram' | 'instagram' | 'threads' | 'twitter' | 'youtube'
  channel_ref     VARCHAR(500)   -- normalized id (@username / chat_id / url)
  title           VARCHAR(300)

  -- GEO: reuse the SAME closed layer set as knowledge_facts so we can cross-query
  geo_layers      JSONB  DEFAULT '[]'   -- subset of ['global','regional','state','city']
  geo_label       VARCHAR(200)          -- human label, e.g. "Ташкент, Узбекистан"

  -- THEMES
  topics          JSONB  DEFAULT '[]'   -- stable channel themes: ["городские новости","транспорт","ЖКХ"]
  tags            JSONB  DEFAULT '[]'   -- salient entities/keywords (like knowledge tags)
  recent_themes   JSONB  DEFAULT '[]'   -- LIVE hot topics: [{"theme":"пробки","count":6,"share":0.3}, ...]

  -- CHARACTERIZATION
  summary         TEXT                  -- 1-2 sentence LLM characterization of the channel
  audience_tone   VARCHAR(200)          -- e.g. "горожане, бытовые жалобы, неформально"
  language        VARCHAR(40)           -- dominant language(s), e.g. "ru, uz"

  -- BOOKKEEPING
  sample_count    INTEGER DEFAULT 0     -- posts used for the last heavy profile
  posts_seen      INTEGER DEFAULT 0     -- cumulative posts observed
  last_profiled_at  TIMESTAMPTZ         -- last HEAVY profile build
  last_themes_at    TIMESTAMPTZ         -- last HOT-themes refresh
  created_at      TIMESTAMPTZ DEFAULT now()
  updated_at      TIMESTAMPTZ DEFAULT now()
  UNIQUE(platform, channel_ref)
```

Migration: add via the `_STAGE23_COLUMNS`-style block in `database.py` (create_all never
ALTERs); new table → `init_tables` create_all picks it up. Model goes in `models.py`.

**Key design choice:** `geo_layers` deliberately mirrors `knowledge_facts.landscape_layers`
so a channel's profile can directly pull its region/city news:
`SELECT … FROM knowledge_facts WHERE landscape_layers && channel.geo_layers ORDER BY created_at DESC`.

---

## 4. The profiler engine — hybrid cadence (agreed)

Two independent refresh rhythms per channel:

### 4a. Heavy profile — rarely (default once / 24h, `PROFILE_TTL_SEC`)
Builds the stable profile. Flow:
1. **Read** ~30–50 recent posts of the channel (MYRMIDON has the Pyrogram session;
   reuse `tg_client.fetch_new_posts` / history — text **and** OCR/caption of media
   posts so text-card channels profile correctly).
2. **One strict-JSON LLM call** (sibling of `classifier.auto_classify_text`) →
   `{geo_layers, geo_label, topics, tags, summary, audience_tone, language}`. The model
   infers geo from the **title + posts** (closed layer set, never invents).
3. **Link to the news base**: query recent `knowledge_facts` overlapping the channel's
   `geo_layers` (and/or `topics`) → a short "what's happening around this channel"
   digest (kept live, not necessarily stored).
4. **Store/update** the `channel_profiles` row; set `last_profiled_at`.

### 4b. Hot themes — often (default every 3–6h, `THEMES_TTL_SEC`)
Keeps "what's discussed lately" current and cheap:
1. Read ~15–20 **newest** posts.
2. Extract recurring themes → frequency. Recommended: a **small LLM call** ("list the
   3–5 recurring topics in these posts") for robustness; cheap keyword/`tags` frequency
   as a fallback. Optionally a Redis sliding window for decay.
3. Update `recent_themes` + `last_themes_at` only (don't touch the heavy fields).

### Where it runs
- **MYRMIDON** owns reading posts (session). The existing `target_engine` per-channel
  loop (every 300s) is the natural scheduler: for each known channel (from
  `agent_channel_prefs` + `mission_targets`), if heavy stale → trigger heavy; if hot
  stale → trigger hot. **Independent per channel.**
- The LLM summarization + storage live in **DAEDALUS** (next to the classifier + the DB).
  MYRMIDON posts the sampled posts to a DAEDALUS internal endpoint; DAEDALUS runs the
  LLM (host Ollama) + cross-refs `knowledge_facts` + writes `channel_profiles`.
- All LLM calls are serial / `keep_alive=0` on the shared ~6 GB GPU; volume is tiny
  (few channels, daily/few-hourly), so cost is negligible. Profiles are **cached in the
  DB** — relevance/comment read the cached profile, **no live profiling at judge time**.

---

## 5. Integration A — relevance (the actual fix)

`handle_relevance` (ORPHEUS) already improved (penalties OFF for classification + a
better prompt). Channel Profiling adds **context** to it. `target_engine` fetches the
channel's cached profile (from DAEDALUS, cached locally) and includes it in the
`_relevance_via_orpheus` request. New prompt shape:

```
Канал: «{title}» — {summary}. Регион: {geo_label}. Тематика канала: {topics}.
Сейчас в канале активно обсуждают: {recent_themes}.
[Свежий контекст региона/города: {news digest}]

Миссия продвигает: {goal}. Позиция: {stance}.
Сообщение в этом канале: "{post}"

С учётом тематики канала и того, что в нём сейчас обсуждают, связано ли сообщение
с темой/проблемой миссии (прямо, косвенно или эмоционально)? ДА или НЕТ.
```

→ `«опять эти машины»` + "канал обсуждает пробки, город Ташкент" → **ДА**.

## 6. Integration B — comment grounding (phase 2)

`assemble_mission_prompt` (ORPHEUS) already injects RAG `fresh_context`. Add a
`[Контекст канала]` block (profile summary + geo + recent_themes + linked news) so
comments sound native to the channel and reference what locals are actually discussing.
The profile flows via the `mission_gen` request (target_engine includes it).

---

## 7. Multi-platform

- `platform` + normalized `channel_ref`; everything downstream (LLM summarization,
  storage, relevance, comment grounding) is **platform-agnostic**.
- A platform driver only has to *feed posts* to the profiler endpoint. The **Telegram**
  driver does this now; Instagram/Threads/YouTube plug in when those drivers work
  (currently broken per `CLAUDE.md` — ADB/AVD), Twitter when integrated.

---

## 8. API surface

Internal (MYRMIDON → DAEDALUS, `X-Internal-Token`):
- `POST /api/v1/channels/internal/profile` — `{platform, channel_ref, title, posts[]}`
  → heavy profile build/update.
- `POST /api/v1/channels/internal/themes` — `{platform, channel_ref, posts[]}` → hot
  themes refresh.
- `GET  /api/v1/channels/internal/profile?platform=&channel_ref=` — fetch cached profile
  (for target_engine to attach to relevance/comment requests).

Operator (phase 2): `GET /api/v1/channels/profiles` + a console screen ("Профили
каналов": topics, geo, hot themes, linked news).

---

## 9. Phased rollout (when we build)

- **Phase 1 (MVP):** `channel_profiles` table + profiler (hybrid) + **relevance
  integration**. Directly fixes `«опять эти машины»`. Telegram. Verifiable end-to-end.
- **Phase 2:** comment grounding (`[Контекст канала]`) + operator UI.
- **Phase 3:** other platforms as their drivers come online.

---

## 10. Open decisions / notes

- **Cadence:** hybrid (heavy daily, hot every 3–6h) — *agreed*. Tune `PROFILE_TTL_SEC`,
  `THEMES_TTL_SEC`, sample sizes via env.
- **Hot-themes method:** small LLM call (robust) vs keyword/`tags` frequency (cheap).
  Recommend LLM with keyword fallback.
- **Geo inference:** from title + posts in the heavy LLM call; closed layer set
  (`global/regional/state/city`) so it cross-queries `knowledge_facts` directly.
- **Relevance foundation:** keep `penalties=False` + the improved prompt (already
  shipped); profiling layers context on top.
- **Identity:** profile is per (platform, channel) — shared across agents, independent.
- **Verification plan (Phase 1):** profile `@tashkent_news333` → expect
  `geo_label≈"Ташкент"`, `topics` incl. transport/utilities, `recent_themes` incl.
  пробки; then relevance on `«опять эти машины»` **with** the profile → ДА, while
  off-topic (weather/book) stays НЕТ.
