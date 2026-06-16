"""
MYRMIDON — Autonomous Target-Channel Engine (P4)
==================================================
Makes channels classified as `target` (and `watching`) actually get acted on. A
background daemon polls each active agent's target channels on its Pyrogram
session, finds NEW posts, keeps only the ones RELEVANT to the agent's interests/
mission, throttles hard (so it never spams), and enqueues a normal execution task
(``generate=True``) onto ``queue:execution_tasks``.

From there the existing pipeline takes over: MYRMIDON reads the post, ORPHEUS
writes a persona/RAG/memory-aware comment (with anti-echo), MYRMIDON posts it and
registers a dialogue watch — so a target comment can grow into a conversation.

Safety rails (anti-spam):
  - only NEW posts (per-channel last-seen id in Redis); the first time a channel is
    seen we record its latest id and comment on nothing (no backlog blast);
  - relevance gate: post text must contain one of the agent's interest/mission
    keywords (no keywords ⇒ the agent stays silent — opt-in by design);
  - at most ``MAX_PER_CHANNEL_PER_HOUR`` comments per channel, one post per channel
    per cycle, and only a capped number of channels scanned per cycle;
  - suspended agents and non-watching channels are skipped.
"""

import json
import logging
import os
import random
import re
import threading
import time
import uuid
from collections import defaultdict
from typing import Optional

import httpx
import redis
from sqlalchemy import text

from app import dialogue_store
from app.telemetry import emit as emit_event

logger = logging.getLogger("myrmidon.target_engine")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
EXECUTION_TASKS_QUEUE = "queue:execution_tasks"
MISSION_GEN_QUEUE = "queue:mission_gen"
RELEVANCE_TIMEOUT = int(os.getenv("TARGET_RELEVANCE_TIMEOUT_SEC", "60"))
DAEDALUS_URL = os.getenv("DAEDALUS_URL", "http://daedalus:8000")
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "morpheus-internal-sync-key")
# Bound how many news posts we classify/ingest per cycle (shared GPU on DAEDALUS).
NEWS_MAX_PER_CYCLE = int(os.getenv("TARGET_NEWS_MAX_PER_CYCLE", "20"))
# How many of the newest new posts per mission channel to LLM-relevance-check.
MISSION_RELEVANCE_CHECK = int(os.getenv("MISSION_RELEVANCE_CHECK", "3"))

# Agent-proposed targets: how many fresh candidate channels per mission to scan per
# cycle, how many recent posts to relevance-check, and how long to wait before
# re-scanning a candidate that didn't pan out (so it isn't re-read every tick).
SUGGEST_MAX_CANDIDATES = int(os.getenv("SUGGEST_MAX_CANDIDATES_PER_MISSION", "3"))
SUGGEST_POSTS_PER_CHANNEL = int(os.getenv("SUGGEST_POSTS_PER_CHANNEL", "3"))
SUGGEST_RELEVANCE_CHECK = int(os.getenv("SUGGEST_RELEVANCE_CHECK", "2"))
SUGGEST_RECHECK_SEC = int(os.getenv("SUGGEST_RECHECK_SEC", "21600"))  # 6h

# Channel profiling (hybrid cadence): heavy profile rarely, hot themes often.
PROFILE_TTL_SEC = int(os.getenv("CHANNEL_PROFILE_TTL_SEC", "86400"))   # 24h heavy
THEMES_TTL_SEC = int(os.getenv("CHANNEL_THEMES_TTL_SEC", "14400"))     # 4h hot themes
PROFILE_SAMPLE = int(os.getenv("CHANNEL_PROFILE_SAMPLE", "40"))
THEMES_SAMPLE = int(os.getenv("CHANNEL_THEMES_SAMPLE", "18"))
PROFILE_MAX_PER_CYCLE = int(os.getenv("CHANNEL_PROFILE_MAX_PER_CYCLE", "3"))
PROFILE_HEAVY_PREFIX = "morpheus:profile:heavy:"     # <platform>:<ref> -> staleness gate
PROFILE_THEMES_PREFIX = "morpheus:profile:themes:"

TARGET_POLL_INTERVAL_SEC = int(os.getenv("TARGET_POLL_INTERVAL_SEC", "300"))
MAX_PER_CHANNEL_PER_HOUR = int(os.getenv("TARGET_MAX_PER_CHANNEL_PER_HOUR", "1"))
# Global cap across ALL channels for one agent — so the swarm never looks like a
# spam bot even with hundreds of target channels.
MAX_PER_AGENT_PER_HOUR = int(os.getenv("TARGET_MAX_PER_AGENT_PER_HOUR", "4"))
PER_CYCLE_CHANNEL_CAP = int(os.getenv("TARGET_PER_CYCLE_CHANNEL_CAP", "25"))
POSTS_PER_CHANNEL = int(os.getenv("TARGET_POSTS_PER_CHANNEL", "5"))
EXEC_DELAY_SEC = int(os.getenv("TARGET_EXEC_DELAY_SEC", "30"))

LASTSEEN_KEY = "morpheus:target:lastseen"     # hash field "<agent>:<chat>" -> post id
RATE_PREFIX = "morpheus:target:rate:"          # per agent:chat hourly counter
SUGGEST_CHECKED_PREFIX = "morpheus:suggest:checked:"  # "<mid>:<ident>" -> recently scanned

_redis: Optional[redis.Redis] = None
_gen_redis: Optional[redis.Redis] = None


def _get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True,
                             socket_connect_timeout=5, socket_timeout=15, retry_on_timeout=True)
    return _redis


def _get_gen_redis() -> redis.Redis:
    """Separate client with a long socket timeout for the ORPHEUS relevance round-trip."""
    global _gen_redis
    if _gen_redis is None:
        _gen_redis = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True,
                                 socket_connect_timeout=5, socket_timeout=RELEVANCE_TIMEOUT + 15)
    return _gen_redis


def _relevance_via_orpheus(agent_id: str, post_text: str, goal: str = "", stance: str = "",
                           channel_profile: Optional[dict] = None) -> Optional[bool]:
    """
    Ask ORPHEUS for a cheap LLM verdict on whether this post is worth commenting on.
    If a mission goal/stance is given, relevance is judged against the MISSION (and, when
    provided, IN the channel's profile context); otherwise against the agent's own
    profile. None on timeout → keyword fallback.
    """
    rid = uuid.uuid4().hex
    rk = f"reply:relevance:{rid}"
    req = {"request_id": rid, "reply_key": rk, "mode": "relevance",
           "agent_id": agent_id, "post_text": post_text, "goal": goal, "stance": stance,
           "channel_profile": channel_profile}
    try:
        c = _get_gen_redis()
        c.lpush(MISSION_GEN_QUEUE, json.dumps(req, ensure_ascii=False))
        res = c.brpop(rk, timeout=RELEVANCE_TIMEOUT)
        if not res:
            return None
        data = json.loads(res[1])
        return bool(data.get("relevant")) if data.get("status") == "ok" else None
    except Exception as exc:
        logger.warning("target_engine: relevance round-trip failed: %s", exc)
        return None


def _agent_keywords(sf, agent_id: str) -> list[str]:
    """Relevance vocabulary = core_interests + meaningful words from core_mission."""
    session = sf()
    try:
        row = session.execute(
            text("SELECT core_interests, core_mission, status FROM agent_profiles WHERE agent_id=:a LIMIT 1"),
            {"a": agent_id},
        ).fetchone()
    except Exception:
        return []
    finally:
        session.close()
    if not row or row[2] == "suspended":
        return []
    kws: set[str] = set()
    interests = row[0] or []
    if isinstance(interests, list):
        for it in interests:
            if isinstance(it, str) and len(it.strip()) >= 3:
                kws.add(it.strip().lower())
    mission = (row[1] or "")
    for w in mission.lower().replace(",", " ").replace(".", " ").split():
        if len(w) >= 5:
            kws.add(w)
    return list(kws)


def _news_channels_by_agent(sf) -> dict[str, list[dict]]:
    """Active agents → their watching NEWS channels (knowledge gathering)."""
    session = sf()
    try:
        rows = session.execute(text(
            "SELECT p.agent_id, p.chat_id, p.username, p.title "
            "FROM agent_channel_prefs p JOIN agent_profiles a ON a.agent_id = p.agent_id "
            "WHERE p.role = 'news' AND p.watching = true AND a.status='active'"
        )).fetchall()
    except Exception as exc:
        logger.error("target_engine: news query failed: %s", exc)
        return {}
    finally:
        session.close()
    out: dict[str, list[dict]] = defaultdict(list)
    for agent_id, chat_id, username, title in rows:
        out[agent_id].append({"chat_id": str(chat_id), "username": username, "title": title})
    return out


def _active_missions(sf) -> list[dict]:
    """
    Active missions with their active CHANNEL targets and roster. Missions are now
    the primary driver: their assigned agents work the mission's targets using the
    mission's goal + stance (instead of each agent's personal interests).
    """
    session = sf()
    try:
        mrows = session.execute(text(
            "SELECT id, title, narrative_goal, stance, tactic FROM missions WHERE status='active'"
        )).fetchall()
        if not mrows:
            return []
        out = []
        for mid, title, goal, stance, tactic in mrows:
            tgts = session.execute(text(
                "SELECT identifier, title FROM mission_targets "
                "WHERE mission_id=:m AND status='active' AND kind='channel'"
            ), {"m": mid}).fetchall()
            squad = session.execute(text(
                "SELECT s.agent_id, s.assigned_role FROM mission_squads s "
                "JOIN agent_profiles a ON a.agent_id = s.agent_id "
                "WHERE s.mission_id=:m AND a.status='active'"
            ), {"m": mid}).fetchall()
            if not tgts or not squad:
                continue
            out.append({
                "id": mid, "title": title, "goal": goal or title, "stance": stance or "",
                "tactic": tactic or "dynamic",
                "channels": [{"identifier": i, "title": t} for i, t in tgts],
                "agents": [{"agent_id": a, "role": r} for a, r in squad],
            })
        return out
    except Exception as exc:
        logger.error("target_engine: missions query failed: %s", exc)
        return []
    finally:
        session.close()


def _news_layers(sf, username: Optional[str], chat_id: str) -> list[str]:
    """Landscape layers configured for this news source (set when marked 'news')."""
    ident = f"@{username}" if username else str(chat_id)
    session = sf()
    try:
        row = session.execute(
            text("SELECT default_layers FROM scraping_landscape WHERE target_identifier=:i LIMIT 1"),
            {"i": ident},
        ).fetchone()
        if row and row[0]:
            return list(row[0])
    except Exception:
        pass
    finally:
        session.close()
    return ["global"]


def _ingest_news(text_content: str, source_url: str, layers: list[str]) -> bool:
    """Send a news post to DAEDALUS for classification/embedding into knowledge."""
    try:
        with httpx.Client(timeout=90.0) as client:
            resp = client.post(
                f"{DAEDALUS_URL}/api/v1/knowledge/internal/ingest",
                json={"content": text_content[:4000], "source_url": source_url, "default_layers": layers},
                headers={"X-Internal-Token": INTERNAL_API_TOKEN},
            )
            resp.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("target_engine: knowledge ingest failed: %s", exc)
        return False


def _relevant(text_content: str, keywords: list[str]) -> bool:
    t = (text_content or "").lower()
    return any(kw in t for kw in keywords)


def _under_cap(r, key: str, cap: int) -> bool:
    return int(r.get(key) or 0) < cap


def _allow_rate(agent_id: str, chat_id: str) -> bool:
    """Hourly per-channel AND per-agent caps. Returns True (and counts) if allowed."""
    r = _get_redis()
    ch_key = f"{RATE_PREFIX}{agent_id}:{chat_id}"
    ag_key = f"{RATE_PREFIX}{agent_id}:_global"
    try:
        if not _under_cap(r, ch_key, MAX_PER_CHANNEL_PER_HOUR):
            return False
        if not _under_cap(r, ag_key, MAX_PER_AGENT_PER_HOUR):
            return False
        pipe = r.pipeline()
        pipe.incr(ch_key); pipe.expire(ch_key, 3600)
        pipe.incr(ag_key); pipe.expire(ag_key, 3600)
        pipe.execute()
        return True
    except Exception:
        return False


def _post_url(username: Optional[str], chat_id: str, post_id: int) -> str:
    if username:
        return f"https://t.me/{username}/{post_id}"
    internal = chat_id[4:] if chat_id.startswith("-100") else chat_id.lstrip("-")
    return f"https://t.me/c/{internal}/{post_id}"


def _enqueue_comment(agent_id: str, url: str, post_text: str, goal: str,
                     stance: str, tactic: str, mission_id: int, channel_label: str,
                     media_context: str = "", channel_profile: Optional[dict] = None) -> None:
    task = {
        "task_id": str(uuid.uuid4()),
        "agent_id": agent_id,
        "target_platform": "telegram",
        "action_type": "comment",
        "target_url": url,
        "text_to_publish": "",          # cognitive only; no canned fallback for autonomous
        "generate": True,
        "narrative_goal": goal or "",
        "stance": stance or "",
        "tactic": tactic or "soft_support",
        "role": "alpha",
        "mission_id": mission_id,
        # The channel's profile — so ORPHEUS grounds the comment in the channel's context.
        "channel_profile": channel_profile,
        # Media already "read" at scan time (caption-less photo/voice posts) — passed
        # through so the comment path doesn't re-analyze it.
        "media_context": media_context or "",
        "execution_delay_sec": EXEC_DELAY_SEC,
        "source": "mission_engine",
        "swarm_seed": True,   # alpha seed → mission's beta/gamma amplify after it posts
    }
    _get_redis().lpush(EXECUTION_TASKS_QUEUE, json.dumps(task, ensure_ascii=False))
    emit_event(agent_id, "target_post",
               f"релевантный пост в {channel_label}: " + post_text[:40],
               status="active", target=url)
    logger.info("mission_engine: agent=%s enqueued comment on %s (mission %s)", agent_id, url, mission_id)


def _mission_post_url(identifier: str, post_id: int) -> str:
    ident = identifier.strip()
    if ident.startswith("@"):
        return f"https://t.me/{ident[1:]}/{post_id}"
    if ident.startswith("http"):
        return f"{ident.rstrip('/')}/{post_id}"
    if ident.lstrip("-").isdigit():
        internal = ident[4:] if ident.startswith("-100") else ident.lstrip("-")
        return f"https://t.me/c/{internal}/{post_id}"
    return f"https://t.me/{ident}/{post_id}"


def _process_mission(mission: dict, sf) -> None:
    """A mission drives its alpha to seed on each target channel's newest relevant
    new post; beta/gamma amplify (mission-scoped) after alpha posts."""
    from app.main import get_agent_credentials
    from app.drivers.tg_client import TelegramDriver
    from app import account_health, schedule

    mid = mission["id"]
    # Pick the seeding alpha from the roster (fall back to any roster agent).
    alphas = [a["agent_id"] for a in mission["agents"] if a["role"] == "alpha"]
    seeder = (alphas or [a["agent_id"] for a in mission["agents"]])[0] if mission["agents"] else None
    if not seeder or account_health.in_cooldown(seeder):
        return
    if not schedule.in_active_hours(sf, seeder):
        return  # persona asleep — don't seed comments outside its active hours
    creds = get_agent_credentials(sf, seeder, "telegram")
    if creds is None:
        return

    channels = mission["channels"][:PER_CYCLE_CHANNEL_CAP]
    fetch_list, since_map = [], {}
    r = _get_redis()
    for ch in channels:
        ident = ch["identifier"]
        key = f"mission:{mid}:{ident}"
        since_map[ident] = int(r.hget(LASTSEEN_KEY, key) or 0)
        username = ident.lstrip("@") if ident.startswith("@") else None
        fetch_list.append({"chat_id": ident, "username": username, "title": ch.get("title")})

    emit_event(seeder, "target_scan",
               f"миссия «{mission['title'][:30]}»: {len(channels)} цел. канал(ов)",
               status="info", target="telegram")

    driver = TelegramDriver(seeder, creds)
    results = driver.fetch_new_posts(fetch_list, since_map, POSTS_PER_CHANNEL)
    label_by_id = {c["identifier"]: (c.get("title") or c["identifier"]) for c in channels}

    for res in results:
        ident = res["chat_id"]
        key = f"mission:{mid}:{ident}"
        if res["newest"]:
            r.hset(LASTSEEN_KEY, key, res["newest"])
        if res["first_seen"]:
            continue
        posts = sorted(res["posts"], key=lambda p: p["post_id"], reverse=True)
        # The channel's profile (topics/geo/hot-themes) lets relevance judge a post IN
        # context (e.g. «опять эти машины» on a Tashkent traffic channel → relevant).
        channel_profile = _get_channel_profile(ident)
        # Phase 2c: attach current news ABOUT the channel's place (facts tagged with its
        # geo terms) so the comment can reference what's actually happening locally.
        if channel_profile and channel_profile.get("geo_label"):
            digest = _geo_news_digest(channel_profile)
            if digest:
                channel_profile = {**channel_profile, "news_digest": digest}
        # Check the few newest new posts (not just the latest) for one relevant to
        # the mission — bounded so the LLM relevance load stays small.
        chosen = None
        chosen_media = ""
        for cand in posts[:MISSION_RELEVANCE_CHECK]:
            ctext = (cand.get("text") or "").strip()
            media_ctx = ""
            # Media-dominant post (caption-less or near-empty): "read" the photo/audio
            # first, so relevance — and the comment — are grounded in the media content
            # instead of the post being silently skipped.
            if cand.get("has_media") and len(ctext) < 30:
                media_ctx = driver.read_media_context(
                    ident, cand["post_id"], _mission_post_url(ident, cand["post_id"]))
            judge_text = (ctext + ("\n" + media_ctx if media_ctx else "")).strip()
            if not judge_text:
                continue  # unreadable media / download failed — nothing to judge on
            v = _relevance_via_orpheus(seeder, judge_text, goal=mission["goal"],
                                       stance=mission["stance"], channel_profile=channel_profile)
            if v is None:
                v = True  # mission target channel → engage if LLM unavailable
            # Surface the per-post relevance decision so the operator sees WHY the bot
            # did/didn't engage (not just silence).
            cand_url = _mission_post_url(ident, cand["post_id"])
            emit_event(seeder, "relevance",
                       ("по теме: " if v else "не по теме: ") + judge_text.replace("\n", " ")[:70],
                       status="ok" if v else "info", target=cand_url)
            _log_decision("relevance", seeder, mid, ident, cand_url,
                          detail=judge_text.replace("\n", " ")[:600], verdict=bool(v))
            if v:
                chosen = cand
                chosen_media = media_ctx
                break
        if not chosen:
            continue
        if not _allow_rate(f"mission_{mid}", ident):
            # Relevant, but the anti-spam hourly cap is spent — say so (else it looks
            # like the bot ignored a clearly on-topic post).
            skip_url = _mission_post_url(ident, chosen["post_id"])
            emit_event(seeder, "rate_skip",
                       f"по теме, но пропуск: лимит {MAX_PER_CHANNEL_PER_HOUR} коммент/час на канал исчерпан",
                       status="warn", target=skip_url)
            _log_decision("skip", seeder, mid, ident, skip_url,
                          detail=f"по теме, но лимит {MAX_PER_CHANNEL_PER_HOUR} коммент/час на канал исчерпан",
                          verdict=True)
            continue
        _enqueue_comment(seeder, _mission_post_url(ident, chosen["post_id"]), chosen["text"],
                         mission["goal"], mission["stance"], mission["tactic"], mid,
                         label_by_id.get(ident, ident), media_context=chosen_media,
                         channel_profile=channel_profile)


def _process_news_agent(agent_id: str, channels: list[dict], sf) -> None:
    """Ingest new posts from an agent's NEWS channels into the knowledge base."""
    from app.main import get_agent_credentials
    from app.drivers.tg_client import TelegramDriver
    from app import account_health
    if account_health.in_cooldown(agent_id):
        return
    creds = get_agent_credentials(sf, agent_id, "telegram")
    if creds is None:
        return
    channels = channels[:PER_CYCLE_CHANNEL_CAP]
    r = _get_redis()
    since_map = {ch["chat_id"]: int(r.hget(LASTSEEN_KEY, f"{agent_id}:{ch['chat_id']}") or 0) for ch in channels}
    driver = TelegramDriver(agent_id, creds)
    results = driver.fetch_new_posts(channels, since_map, POSTS_PER_CHANNEL)
    if not results:
        return
    by_id = {c["chat_id"]: c for c in channels}
    news_budget = NEWS_MAX_PER_CYCLE
    for res in results:
        cid = res["chat_id"]
        if res["newest"]:
            r.hset(LASTSEEN_KEY, f"{agent_id}:{cid}", res["newest"])
        if res["first_seen"]:
            continue
        channel = by_id.get(cid, {"chat_id": cid})
        layers = _news_layers(sf, channel.get("username"), cid)
        ingested = 0
        for post in res["posts"]:
            if news_budget <= 0:
                break
            if _ingest_news(post["text"], _post_url(channel.get("username"), cid, post["post_id"]), layers):
                ingested += 1
                news_budget -= 1
        if ingested:
            emit_event(agent_id, "news_ingest",
                       f"загрузил {ingested} новост. из {channel.get('title') or cid}",
                       status="ok", target=channel.get("username") or cid)


# ── Agent-proposed targets ───────────────────────────────────────────────────

def _norm_ident(ident: str) -> str:
    """Normalize a channel identifier for dedup comparison (@user / t.me url / id)."""
    s = (ident or "").strip().lower()
    for pref in ("https://t.me/", "http://t.me/", "t.me/"):
        if s.startswith(pref):
            s = s[len(pref):]
            break
    s = s.lstrip("@").strip("/")
    if "/" in s and not s.startswith("c/"):
        s = s.split("/")[0]  # drop a trailing /<post_id>, keep the channel part
    return s


def _suggest_target(mission_id: int, identifier: str, title: str, proposed_by: str,
                    reason: str, kind: str = "channel") -> bool:
    """POST an agent-proposed target to DAEDALUS (status=suggested). True if created."""
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                f"{DAEDALUS_URL}/api/v1/missions/internal/suggest-target",
                json={"mission_id": mission_id, "identifier": identifier, "kind": kind,
                      "title": title, "proposed_by": proposed_by, "reason": reason},
                headers={"X-Internal-Token": INTERNAL_API_TOKEN},
            )
            resp.raise_for_status()
            return resp.json().get("status") == "created"
    except Exception as exc:
        logger.warning("suggest_engine: suggest-target failed: %s", exc)
        return False


def _mission_target_idents(sf, mid: int) -> set[str]:
    """All identifiers already on the mission (any status) — normalized, for dedup."""
    session = sf()
    try:
        rows = session.execute(
            text("SELECT identifier FROM mission_targets WHERE mission_id=:m"), {"m": mid}
        ).fetchall()
    except Exception:
        return set()
    finally:
        session.close()
    return {_norm_ident(r[0]) for r in rows}


def _roster_candidate_channels(sf, mid: int) -> list[dict]:
    """Watching target/news channels of the mission roster's active agents."""
    session = sf()
    try:
        rows = session.execute(text(
            "SELECT DISTINCT p.agent_id, p.chat_id, p.username, p.title "
            "FROM agent_channel_prefs p "
            "JOIN mission_squads s ON s.agent_id = p.agent_id "
            "JOIN agent_profiles a ON a.agent_id = p.agent_id "
            "WHERE s.mission_id=:m AND p.watching=true AND p.role IN ('target','news') "
            "AND a.status='active'"
        ), {"m": mid}).fetchall()
    except Exception as exc:
        logger.error("suggest_engine: candidate query failed: %s", exc)
        return []
    finally:
        session.close()
    return [{"agent_id": a, "chat_id": str(c), "username": u, "title": t} for a, c, u, t in rows]


def _suggest_targets_for_mission(mission: dict, sf) -> None:
    """
    Roster agents read their OWN channels; any channel relevant to the mission that
    isn't already a target is proposed (status=suggested, source=agent) for the
    operator to approve. Throttled: a few fresh candidates per cycle, each re-scanned
    at most once per ``SUGGEST_RECHECK_SEC``; already-present/rejected channels are
    skipped (the API also dedups, so a declined target is never re-spammed).
    """
    from app.main import get_agent_credentials
    from app.drivers.tg_client import TelegramDriver
    from app import account_health

    mid = mission["id"]
    existing = _mission_target_idents(sf, mid)
    r = _get_redis()

    candidates = []
    for ch in _roster_candidate_channels(sf, mid):
        username = ch.get("username")
        ident = f"@{username}" if username else ch["chat_id"]
        norm = _norm_ident(ident)
        if norm in existing:
            continue
        checked_key = f"{SUGGEST_CHECKED_PREFIX}{mid}:{norm}"
        if r.get(checked_key):
            continue
        ch["ident"] = ident
        ch["checked_key"] = checked_key
        candidates.append(ch)

    if not candidates:
        return
    random.shuffle(candidates)
    candidates = candidates[:SUGGEST_MAX_CANDIDATES]

    # Group by agent so each Telegram session opens once.
    by_agent: dict[str, list[dict]] = defaultdict(list)
    for c in candidates:
        by_agent[c["agent_id"]].append(c)

    for agent_id, chans in by_agent.items():
        if account_health.in_cooldown(agent_id):
            continue
        creds = get_agent_credentials(sf, agent_id, "telegram")
        if creds is None:
            continue
        emit_event(agent_id, "target_recon",
                   f"ищет новые цели для «{mission['title'][:25]}»: {len(chans)} канал(ов)",
                   status="info", target="telegram")
        fetch_list = [{"chat_id": c["ident"], "username": c.get("username") or None} for c in chans]
        since_map = {c["ident"]: 0 for c in chans}  # read the latest posts, not "new only"
        driver = TelegramDriver(agent_id, creds)
        results = driver.fetch_new_posts(fetch_list, since_map, SUGGEST_POSTS_PER_CHANNEL)
        res_by_id = {res["chat_id"]: res for res in results}
        for c in chans:
            # Mark scanned regardless of outcome (anti-rescan / anti-spam).
            r.set(c["checked_key"], "1", ex=SUGGEST_RECHECK_SEC)
            res = res_by_id.get(c["ident"])
            if not res or not res["posts"]:
                continue
            posts = sorted(res["posts"], key=lambda p: p["post_id"], reverse=True)
            hit = None
            for cand in posts[:SUGGEST_RELEVANCE_CHECK]:
                # Conservative: only propose on a positive verdict (None/False → skip),
                # since this channel is NOT yet a mission target.
                if _relevance_via_orpheus(agent_id, cand["text"], goal=mission["goal"],
                                          stance=mission["stance"]):
                    hit = cand
                    break
            if not hit:
                continue
            title = c.get("title") or c["ident"]
            reason = f"Свежий пост в тему миссии: «{hit['text'][:120]}»"
            if _suggest_target(mid, c["ident"], title, agent_id, reason):
                emit_event(agent_id, "target_suggest",
                           f"предложил канал {title[:30]} в миссию «{mission['title'][:25]}»",
                           status="ok", target=c["ident"])
                logger.info("suggest_engine: agent=%s proposed %s for mission %s",
                            agent_id, c["ident"], mid)


# ── Channel profiling (hybrid: heavy profile rarely, hot themes often) ────────

def _get_channel_profile(ident: str) -> Optional[dict]:
    """Fetch a channel's cached profile from DAEDALUS (None if missing/unavailable)."""
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(
                f"{DAEDALUS_URL}/api/v1/channels/internal/profile",
                params={"platform": "telegram", "channel_ref": ident},
                headers={"X-Internal-Token": INTERNAL_API_TOKEN},
            )
            resp.raise_for_status()
            data = resp.json()
        return data.get("profile") if data.get("status") == "ok" else None
    except Exception:
        return None


def _geo_news_digest(profile: dict, limit: int = 4) -> list:
    """Recent news ABOUT the channel's place (facts tagged with its geo terms) — to ground
    comments in the region's current events (Phase 2c). Layers are only a scope, so we
    match on place terms derived from geo_label. Best-effort; HTML stripped."""
    geo_label = profile.get("geo_label") or ""
    terms = [w for w in (x.strip(" ,.;:").lower() for x in geo_label.replace(",", " ").split())
             if len(w) >= 4]
    if not terms:
        return []
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(
                f"{DAEDALUS_URL}/api/v1/knowledge/internal/by-geo",
                params={"terms": ",".join(terms), "limit": limit},
                headers={"X-Internal-Token": INTERNAL_API_TOKEN},
            )
            resp.raise_for_status()
        out = []
        for f in resp.json().get("facts", []):
            c = re.sub(r"<[^>]+>", " ", f.get("content") or "")  # strip HTML
            c = " ".join(c.split()).strip()
            if c:
                out.append(c)
        return out
    except Exception:
        return []


def _log_decision(kind: str, agent_id: str, mission_id, channel_ref: str, post_url: str,
                  detail: str = "", verdict=None) -> None:
    """Durably record a decision (relevance/skip) to DAEDALUS for the operator's history.
    Best-effort — never blocks the engine."""
    try:
        with httpx.Client(timeout=8.0) as client:
            client.post(
                f"{DAEDALUS_URL}/api/v1/decisions/internal/log",
                json={"kind": kind, "agent_id": agent_id, "mission_id": mission_id,
                      "platform": "telegram", "channel_ref": channel_ref, "post_url": post_url,
                      "detail": detail, "verdict": verdict},
                headers={"X-Internal-Token": INTERNAL_API_TOKEN},
            )
    except Exception as exc:
        logger.debug("decision log failed: %s", exc)


def _build_channel_profile(ident: str, title: str, posts: list[str]) -> bool:
    try:
        with httpx.Client(timeout=150.0) as client:
            resp = client.post(
                f"{DAEDALUS_URL}/api/v1/channels/internal/profile",
                json={"platform": "telegram", "channel_ref": ident, "title": title or "", "posts": posts},
                headers={"X-Internal-Token": INTERNAL_API_TOKEN},
            )
            resp.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("profiler: build_profile failed for %s: %s", ident, exc)
        return False


def _refresh_channel_themes(ident: str, posts: list[str]) -> bool:
    try:
        with httpx.Client(timeout=90.0) as client:
            resp = client.post(
                f"{DAEDALUS_URL}/api/v1/channels/internal/themes",
                json={"platform": "telegram", "channel_ref": ident, "posts": posts},
                headers={"X-Internal-Token": INTERNAL_API_TOKEN},
            )
            resp.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("profiler: refresh_themes failed for %s: %s", ident, exc)
        return False


def _profile_channels_for_mission(mission: dict, sf) -> None:
    """
    Keep each mission target channel's profile current (independent per channel),
    hybrid cadence: heavy profile every PROFILE_TTL_SEC, hot themes every
    THEMES_TTL_SEC. Staleness gated by Redis markers so most cycles do nothing (no
    session opened); only stale channels are read.
    """
    from app.main import get_agent_credentials
    from app.drivers.tg_client import TelegramDriver
    from app import account_health

    r = _get_redis()
    todo = []  # (ident, title, heavy_key, themes_key, need_heavy, need_themes)
    for ch in mission["channels"]:
        ident = ch["identifier"]
        nref = _norm_ident(ident)
        heavy_key = f"{PROFILE_HEAVY_PREFIX}telegram:{nref}"
        themes_key = f"{PROFILE_THEMES_PREFIX}telegram:{nref}"
        need_heavy = not r.get(heavy_key)
        need_themes = not r.get(themes_key)
        if need_heavy or need_themes:
            todo.append((ident, ch.get("title"), heavy_key, themes_key, need_heavy, need_themes))
    if not todo:
        return

    alphas = [a["agent_id"] for a in mission["agents"] if a["role"] == "alpha"]
    reader = (alphas or [a["agent_id"] for a in mission["agents"]])[0] if mission["agents"] else None
    if not reader or account_health.in_cooldown(reader):
        return
    creds = get_agent_credentials(sf, reader, "telegram")
    if creds is None:
        return
    driver = TelegramDriver(reader, creds)

    for ident, title, heavy_key, themes_key, need_heavy, need_themes in todo[:PROFILE_MAX_PER_CYCLE]:
        username = ident.lstrip("@") if ident.startswith("@") else None
        limit = PROFILE_SAMPLE if need_heavy else THEMES_SAMPLE
        results = driver.fetch_new_posts([{"chat_id": ident, "username": username}], {ident: 0}, limit)
        posts = [p["text"] for p in (results[0]["posts"] if results else []) if p.get("text")]
        if not posts:
            continue
        if need_heavy:
            if _build_channel_profile(ident, title, posts[:PROFILE_SAMPLE]):
                r.set(heavy_key, "1", ex=PROFILE_TTL_SEC)
                r.set(themes_key, "1", ex=THEMES_TTL_SEC)  # heavy also seeds hot themes
                emit_event(reader, "channel_profile",
                           f"профилирует канал {title or ident}", status="info", target=ident)
        elif need_themes:
            if _refresh_channel_themes(ident, posts[:THEMES_SAMPLE]):
                r.set(themes_key, "1", ex=THEMES_TTL_SEC)


def _poll_once(sf) -> None:
    # 1) Mission-driven commenting (missions are the primary driver).
    for mission in _active_missions(sf):
        # 1a) Channel profiling FIRST (hybrid cadence, mostly a no-op via Redis gates) so
        # relevance always judges posts in the channel's freshest context — otherwise a
        # cold-start channel's first relevant posts are judged blind and lost to lastseen.
        try:
            _profile_channels_for_mission(mission, sf)
        except Exception as exc:
            logger.error("profiler: mission %s failed: %s", mission.get("id"), exc)
        # 1b) Seed/amplify comments on the mission's target channels.
        try:
            _process_mission(mission, sf)
        except Exception as exc:
            logger.error("mission_engine: mission %s failed: %s", mission.get("id"), exc)
        # 1c) Agent-proposed targets: roster bots read their own channels and propose
        # mission-relevant ones the operator hasn't added yet (status=suggested).
        try:
            _suggest_targets_for_mission(mission, sf)
        except Exception as exc:
            logger.error("suggest_engine: mission %s failed: %s", mission.get("id"), exc)
    # 2) News ingest (knowledge gathering, per agent).
    for agent_id, channels in _news_channels_by_agent(sf).items():
        try:
            _process_news_agent(agent_id, channels, sf)
        except Exception as exc:
            logger.error("news_ingest: agent %s failed: %s", agent_id, exc)


def _run_loop(sf) -> None:
    logger.info("Target engine started (poll every %ds, max %d/channel/hour).",
                TARGET_POLL_INTERVAL_SEC, MAX_PER_CHANNEL_PER_HOUR)
    while True:
        try:
            _poll_once(sf)
        except Exception as exc:
            logger.error("Target engine tick crashed: %s", exc)
        time.sleep(TARGET_POLL_INTERVAL_SEC)


def start_target_engine(db_session_factory) -> None:
    """Launch the target-channel poller as a daemon thread (call once from main)."""
    t = threading.Thread(target=_run_loop, args=(db_session_factory,), daemon=True, name="target-engine")
    t.start()
