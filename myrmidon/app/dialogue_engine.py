"""
MYRMIDON — Autonomous Dialogue Engine
=======================================
The piece that makes a bot behave like a real person *after* it comments: it
keeps the conversation going. A background daemon thread polls every comment the
bot has left (the "watches" in :mod:`app.dialogue_store`); whenever a real human
replies to one of those comments, it asks ORPHEUS for a context/memory/mood-aware
answer and posts it — then watches its own answer so the exchange can run for
several human-like turns.

Why polling (not a live MTProto handler): each agent has a single Pyrogram
session shared with mission posting and the scouting radar. A persistent
update-listener would fight those for the AUTH_KEY. Polling lets the engine grab
the per-agent session lock for a few seconds, drain new replies, and let go — so
it never collides with a mission.

Flow per tick:
  1. Load all live watches, group by agent.
  2. For each agent with TG credentials, run one dialogue cycle (the driver opens
     the session once, scans every watch for new human replies, answers them).
  3. Persist bookkeeping: advance last-seen ids, register follow-up watches on the
     bot's own answers, retire watches that reached max conversation depth.
"""

import json
import logging
import os
import threading
import time
import uuid
from collections import defaultdict
from typing import Optional

import redis

from app import dialogue_store
from app.dialogue_store import (
    DIALOGUE_POLL_INTERVAL_SEC,
    DIALOGUE_MAX_DEPTH,
)
from app.telemetry import emit as emit_event

logger = logging.getLogger("myrmidon.dialogue_engine")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
MISSION_GEN_QUEUE = "queue:mission_gen"
ORPHEUS_GEN_TIMEOUT = int(os.getenv("ORPHEUS_GEN_TIMEOUT_SEC", "150"))

_gen_redis: Optional[redis.Redis] = None


def _get_gen_redis() -> redis.Redis:
    global _gen_redis
    if _gen_redis is None:
        _gen_redis = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=0,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=ORPHEUS_GEN_TIMEOUT + 15,
        )
    return _gen_redis


def generate_reply_via_orpheus(payload: dict) -> str:
    """
    Ask ORPHEUS to answer a human who replied to the bot. Reuses the mission-gen
    request/reply channel with ``mode='reply'`` so ORPHEUS pulls the agent's
    persona, RAG knowledge, per-person memory and the thread mood. Blocking;
    returns '' on timeout/failure (the engine then just skips this reply).
    """
    request_id = str(uuid.uuid4())
    reply_key = f"reply:missiongen:{request_id}"
    req = dict(payload)
    req["request_id"] = request_id
    req["reply_key"] = reply_key
    try:
        client = _get_gen_redis()
        client.lpush(MISSION_GEN_QUEUE, json.dumps(req, ensure_ascii=False))
        res = client.brpop(reply_key, timeout=ORPHEUS_GEN_TIMEOUT)
        if not res:
            logger.warning("Dialogue reply gen timed out (%ds) for agent %s.",
                           ORPHEUS_GEN_TIMEOUT, payload.get("agent_id"))
            return ""
        _, raw = res
        data = json.loads(raw)
        if data.get("status") == "ok" and data.get("text"):
            return data["text"]
        logger.warning("Dialogue reply gen returned no text (%s).", data.get("reason"))
        return ""
    except Exception as exc:
        logger.error("Dialogue reply gen round-trip failed: %s", exc)
        return ""


def _process_agent(agent_id: str, watches: list, db_session_factory) -> None:
    from app.main import get_agent_credentials  # late import to avoid a cycle
    from app.drivers.tg_client import TelegramDriver

    credentials = get_agent_credentials(db_session_factory, agent_id, "telegram")
    if credentials is None:
        # Account got unbound/suspended — drop its watches so we stop polling it.
        for w in watches:
            dialogue_store.remove_watch(w["watch_id"])
        return

    emit_event(agent_id, "poll", f"проверяет {len(watches)} диалог(ов) на новые ответы",
               status="active", target="telegram")
    driver = TelegramDriver(agent_id, credentials)
    results = driver.run_dialogue_cycle(watches, generate_reply_via_orpheus, DIALOGUE_MAX_DEPTH)

    # Advance last-seen markers so we don't re-scan the same replies next tick.
    for watch_id, max_id in results.get("updates", {}).items():
        dialogue_store.update_watch(watch_id, last_seen_reply_id=max_id)

    # Retire watches that hit max depth (signalled as ("__expire__", watch_id)).
    for entry in results.get("handled", []):
        if isinstance(entry, tuple) and entry and entry[0] == "__expire__":
            dialogue_store.remove_watch(entry[1])

    # Register follow-up watches on the bot's own answers → multi-turn dialogue.
    for nw in results.get("new_watches", []):
        dialogue_store.register_watch(agent_id=agent_id, **nw)


def _poll_once(db_session_factory) -> None:
    watches = dialogue_store.list_watches()
    if not watches:
        return
    by_agent: dict[str, list] = defaultdict(list)
    for w in watches:
        by_agent[w["agent_id"]].append(w)

    logger.debug("Dialogue tick: %d watches across %d agents.", len(watches), len(by_agent))
    for agent_id, agent_watches in by_agent.items():
        try:
            _process_agent(agent_id, agent_watches, db_session_factory)
        except Exception as exc:
            logger.error("Dialogue processing failed for agent %s: %s", agent_id, exc)


def _run_loop(db_session_factory) -> None:
    logger.info("Dialogue engine started (poll every %ds, max depth %d).",
                DIALOGUE_POLL_INTERVAL_SEC, DIALOGUE_MAX_DEPTH)
    while True:
        try:
            _poll_once(db_session_factory)
        except Exception as exc:
            logger.error("Dialogue engine tick crashed: %s", exc)
        time.sleep(DIALOGUE_POLL_INTERVAL_SEC)


def start_dialogue_engine(db_session_factory) -> None:
    """Launch the dialogue poller as a daemon thread (call once from main)."""
    t = threading.Thread(target=_run_loop, args=(db_session_factory,), daemon=True, name="dialogue-engine")
    t.start()
