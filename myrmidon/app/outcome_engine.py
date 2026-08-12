"""
MYRMIDON — Mission outcome measurement (Stage 42)
===================================================
The second reading of a discussion the swarm entered.

The operator defines success as: the tone of the discussion changed, and real people
were drawn into dialogue. Both halves were previously unobservable — the entry mood was
computed to pick a tactic and discarded, and human replies were logged without a link to
the mission that provoked them. `mission_outcomes` records the "before"; this loop comes
back some hours later and records the "after".

Deliberate choices:

* the SAME 3-way judgement (AGREE/NEUTRAL/OPPOSE) as the entry reading, asked with the
  same prompt in ORPHEUS — a delta between two differently-worded questions is noise;
* a thread that did not grow is reported as such rather than as "tone unchanged". The
  two are different outcomes and collapsing them would report silence as success;
* a thread we can no longer read closes the row as `unreadable` with a NULL verdict.
  "We do not know" is honest; "no change" would be a fabrication;
* read-only. This loop never posts, joins or reacts.
"""

import json
import logging
import os
import threading
import time
import uuid
from typing import Optional

import httpx
import redis

logger = logging.getLogger("myrmidon.outcome_engine")

DAEDALUS_URL = os.getenv("DAEDALUS_URL", "http://daedalus:8000")
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "morpheus-internal-sync-key")
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
MISSION_GEN_QUEUE = "queue:mission_gen"

# Slow on purpose: this is bookkeeping, not the swarm's job. It also shares the single
# GPU with generation, so it must never crowd it out.
POLL_INTERVAL_SEC = int(os.getenv("OUTCOME_POLL_INTERVAL", "900"))
BATCH = int(os.getenv("OUTCOME_BATCH", "5"))
MOOD_TIMEOUT_SEC = int(os.getenv("OUTCOME_MOOD_TIMEOUT", "120"))
COMMENT_READ_LIMIT = int(os.getenv("OUTCOME_COMMENT_LIMIT", "60"))

_redis: Optional[redis.Redis] = None


def _get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True,
                             socket_connect_timeout=5, socket_timeout=MOOD_TIMEOUT_SEC + 30)
    return _redis


def _pending() -> list[dict]:
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(f"{DAEDALUS_URL}/api/v1/missions/internal/outcomes/pending",
                              params={"limit": BATCH},
                              headers={"X-Internal-Token": INTERNAL_API_TOKEN})
            resp.raise_for_status()
            return resp.json().get("outcomes") or []
    except Exception as exc:
        logger.debug("outcome_engine: cannot fetch pending: %s", exc)
        return []


def _report(outcome_id: int, mood: Optional[str], size: int,
            replies: int, unreadable: bool = False) -> None:
    try:
        with httpx.Client(timeout=20.0) as client:
            client.post(f"{DAEDALUS_URL}/api/v1/missions/internal/outcome-measure",
                        json={"outcome_id": outcome_id, "mood_after": mood,
                              "thread_size_after": size, "human_replies": replies,
                              "unreadable": unreadable},
                        headers={"X-Internal-Token": INTERNAL_API_TOKEN})
    except Exception as exc:
        logger.warning("outcome_engine: report failed for %s: %s", outcome_id, exc)


def _mood_via_orpheus(side: str, post_text: str, thread_text: str) -> Optional[str]:
    """Ask ORPHEUS for the same 3-way verdict that was taken on entry."""
    request_id = str(uuid.uuid4())
    reply_key = f"reply:missiongen:{request_id}"
    req = {"request_id": request_id, "reply_key": reply_key, "mode": "mood",
           "our_side": side, "post_text": post_text, "thread_context": thread_text}
    try:
        client = _get_redis()
        client.lpush(MISSION_GEN_QUEUE, json.dumps(req, ensure_ascii=False))
        res = client.brpop(reply_key, timeout=MOOD_TIMEOUT_SEC)
        if not res:
            return None
        return (json.loads(res[1]) or {}).get("mood")
    except Exception as exc:
        logger.debug("outcome_engine: mood round-trip failed: %s", exc)
        return None


def _measure_one(sf, outcome: dict) -> None:
    """Re-read one discussion and report what became of it."""
    from app.main import get_agent_credentials
    from app.drivers.tg_client import TelegramDriver, parse_target

    chat_ref, post_id = parse_target(outcome.get("post_url") or "")
    if not chat_ref or not post_id:
        _report(outcome["id"], None, 0, 0, unreadable=True)
        return

    # Any live account can read; this is measurement, not action.
    agent_id = _any_reader(sf)
    if not agent_id:
        return                                   # nothing to read with — retry next tick
    creds = get_agent_credentials(sf, agent_id, "telegram")
    if creds is None:
        return
    data = TelegramDriver(agent_id, creds).export_thread(
        str(chat_ref), post_limit=1, comment_limit=COMMENT_READ_LIMIT, post_id=post_id)
    posts = data.get("posts") or []
    if data.get("error") or not posts:
        logger.info("outcome_engine: thread %s unreadable — closing without a verdict.",
                    outcome.get("post_url"))
        _report(outcome["id"], None, 0, 0, unreadable=True)
        return

    post = posts[0]
    comments = post.get("comments") or []
    ours = {c["id"] for c in comments if c.get("is_self")}
    # Engagement = a real person answering one of OUR comments, not merely talking.
    human_replies = sum(1 for c in comments
                        if c.get("parent_id") in ours and not c.get("is_self"))

    thread_text = "\n".join(f"{c.get('author', 'кто-то')}: {c.get('text', '')}"
                            for c in comments[-25:])
    mood = _mood_via_orpheus(outcome.get("our_side") or outcome.get("stance") or "",
                             post.get("text") or "", thread_text)
    _report(outcome["id"], mood, len(comments), human_replies)
    logger.info("outcome_engine: %s — тон %s → %s, комментариев %d→%d, ответов людей %d",
                outcome.get("post_url"), outcome.get("mood_before"), mood,
                outcome.get("thread_size_before") or 0, len(comments), human_replies)


def _any_reader(sf) -> Optional[str]:
    """Any agent with a live Telegram account — reading needs no particular persona."""
    from sqlalchemy import text as sql
    try:
        with sf() as session:
            row = session.execute(sql(
                "SELECT a.agent_id FROM agent_profiles a "
                "JOIN souls_accounts s ON s.agent_id = a.agent_id "
                "WHERE s.platform='telegram' AND s.status='active' AND a.status='active' "
                "LIMIT 1")).fetchone()
            return row[0] if row else None
    except Exception as exc:
        logger.debug("outcome_engine: no reader available: %s", exc)
        return None


def _run_loop(sf) -> None:
    logger.info("Outcome engine started (every %ds, %d per tick).", POLL_INTERVAL_SEC, BATCH)
    while True:
        try:
            for outcome in _pending():
                try:
                    _measure_one(sf, outcome)
                except Exception as exc:
                    logger.warning("outcome_engine: measuring %s failed: %s", outcome.get("id"), exc)
        except Exception as exc:
            logger.error("Outcome engine tick crashed: %s", exc)
        time.sleep(POLL_INTERVAL_SEC)


def start_outcome_engine(db_session_factory) -> None:
    """Launch the outcome measurer as a daemon thread (call once from main)."""
    t = threading.Thread(target=_run_loop, args=(db_session_factory,), daemon=True,
                         name="outcome-engine")
    t.start()
