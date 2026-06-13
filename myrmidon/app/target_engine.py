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


def _relevance_via_orpheus(agent_id: str, post_text: str) -> Optional[bool]:
    """
    Ask ORPHEUS for a cheap LLM verdict on whether this post is worth commenting on
    (mission/interests-aware). Returns True/False, or None on timeout/failure so the
    caller can fall back to the keyword check.
    """
    rid = uuid.uuid4().hex
    rk = f"reply:relevance:{rid}"
    req = {"request_id": rid, "reply_key": rk, "mode": "relevance",
           "agent_id": agent_id, "post_text": post_text}
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


def _channels_by_agent(sf) -> dict[str, list[dict]]:
    """Active agents → their watching channels (role target OR news) to act on."""
    session = sf()
    try:
        rows = session.execute(text(
            "SELECT p.agent_id, p.chat_id, p.username, p.title, p.role "
            "FROM agent_channel_prefs p JOIN agent_profiles a ON a.agent_id = p.agent_id "
            "WHERE p.role IN ('target','news') AND p.watching = true AND a.status='active'"
        )).fetchall()
    except Exception as exc:
        logger.error("target_engine: query failed: %s", exc)
        return {}
    finally:
        session.close()
    out: dict[str, list[dict]] = defaultdict(list)
    for agent_id, chat_id, username, title, role in rows:
        out[agent_id].append({"chat_id": str(chat_id), "username": username, "title": title, "role": role})
    return out


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


def _enqueue_comment(agent_id: str, channel: dict, post: dict, mission: str) -> None:
    url = _post_url(channel.get("username"), channel["chat_id"], post["post_id"])
    task = {
        "task_id": str(uuid.uuid4()),
        "agent_id": agent_id,
        "target_platform": "telegram",
        "action_type": "comment",
        "target_url": url,
        "text_to_publish": "",          # cognitive only; no canned fallback for autonomous
        "generate": True,
        "narrative_goal": mission or "",
        "tactic": "soft_support",
        "role": "alpha",
        "execution_delay_sec": EXEC_DELAY_SEC,
        "source": "target_engine",
        "swarm_seed": True,   # alpha seed → swarm amplifies after it posts
    }
    _get_redis().lpush(EXECUTION_TASKS_QUEUE, json.dumps(task, ensure_ascii=False))
    emit_event(agent_id, "target_post",
               f"релевантный пост в {channel.get('title') or channel['chat_id']}: " + post["text"][:40],
               status="active", target=url)
    logger.info("target_engine: agent=%s enqueued comment on %s", agent_id, url)


def _process_agent(agent_id: str, channels: list[dict], sf) -> None:
    from app.main import get_agent_credentials
    from app.drivers.tg_client import TelegramDriver

    creds = get_agent_credentials(sf, agent_id, "telegram")
    if creds is None:
        return
    keywords = _agent_keywords(sf, agent_id)  # relevance basis for 'target' channels

    channels = channels[:PER_CYCLE_CHANNEL_CAP]
    r = _get_redis()
    since_map = {}
    for ch in channels:
        v = r.hget(LASTSEEN_KEY, f"{agent_id}:{ch['chat_id']}")
        since_map[ch["chat_id"]] = int(v) if v else 0

    n_target = sum(1 for c in channels if c.get("role") == "target")
    emit_event(agent_id, "target_scan",
               f"проверяет {n_target} цел. / {len(channels) - n_target} новост. канал(ов)",
               status="info", target="telegram")

    driver = TelegramDriver(agent_id, creds)
    results = driver.fetch_new_posts(channels, since_map, POSTS_PER_CHANNEL)
    if not results:
        return

    mission = ""
    ks = sf()
    try:
        row = ks.execute(text("SELECT core_mission FROM agent_profiles WHERE agent_id=:a"), {"a": agent_id}).fetchone()
        mission = (row[0] if row else "") or ""
    except Exception:
        pass
    finally:
        ks.close()

    by_id = {c["chat_id"]: c for c in channels}
    news_budget = NEWS_MAX_PER_CYCLE
    for res in results:
        cid = res["chat_id"]
        # Advance last-seen regardless, so we never re-scan old posts.
        if res["newest"]:
            r.hset(LASTSEEN_KEY, f"{agent_id}:{cid}", res["newest"])
        if res["first_seen"]:
            continue  # just learned this channel's position; don't blast its backlog
        channel = by_id.get(cid, {"chat_id": cid})
        role = channel.get("role", "target")

        if role == "target":
            # Evaluate only the newest new post (bounds LLM relevance to ≤1/channel/cycle).
            posts = sorted(res["posts"], key=lambda p: p["post_id"], reverse=True)
            if not posts:
                continue
            top = posts[0]
            # Hybrid relevance: an operator-listed interest keyword is authoritative
            # (engage, no LLM needed). Otherwise ask ORPHEUS for a smart verdict — this
            # catches posts that match the persona/mission without an exact keyword.
            if keywords and _relevant(top["text"], keywords):
                verdict = True
            else:
                verdict = _relevance_via_orpheus(agent_id, top["text"]) or False
            if not verdict:
                continue
            if not _allow_rate(agent_id, cid):
                continue
            _enqueue_comment(agent_id, channel, top, mission)

        elif role == "news":
            # Ingest new posts into the knowledge base (no relevance gate — it's
            # world-knowledge gathering); bounded per cycle to spare the GPU.
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


def _poll_once(sf) -> None:
    by_agent = _channels_by_agent(sf)
    if not by_agent:
        return
    logger.debug("target_engine tick: %d agents with watched channels", len(by_agent))
    for agent_id, channels in by_agent.items():
        try:
            _process_agent(agent_id, channels, sf)
        except Exception as exc:
            logger.error("target_engine: agent %s failed: %s", agent_id, exc)


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
