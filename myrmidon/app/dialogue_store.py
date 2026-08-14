"""
MYRMIDON — Dialogue Store (Redis-backed conversation registry)
================================================================
Shared, dependency-light state for the autonomous dialogue subsystem. Used by
both the Telegram driver (which *registers* a watch right after it posts a
comment) and the dialogue engine (which *polls* those watches for human replies
and answers them).

A "watch" is a single comment the bot left under a channel post that we keep
listening on: if a real human replies to that exact comment, the engine wakes
up, generates a context/memory/mood-aware answer and posts it — then registers a
fresh watch on its own answer so the conversation can continue for several turns.

Three pieces of state live here, all in Redis (so MYRMIDON restarts keep the
conversations alive):
  - ``WATCHES_KEY`` hash:   watch_id -> JSON watch record
  - ``HANDLED_KEY`` set:    "<disc_chat>:<msg_id>" of replies already answered
  - session lock:           per-agent advisory lock so the poller and a mission
                            never drive the same Pyrogram session at once
                            (shared AUTH_KEY → desync/relogin risk).
"""

import json
import logging
import os
import time
import uuid
from typing import Optional

import redis

logger = logging.getLogger("myrmidon.dialogue_store")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

WATCHES_KEY = "morpheus:dialogue:watches"
HANDLED_KEY = "morpheus:dialogue:handled"
SESSION_LOCK_PREFIX = "morpheus:tg_lock:"

# Tunables (env-overridable).
DIALOGUE_POLL_INTERVAL_SEC = int(os.getenv("DIALOGUE_POLL_INTERVAL_SEC", "90"))
DIALOGUE_MAX_DEPTH = int(os.getenv("DIALOGUE_MAX_DEPTH", "6"))
DIALOGUE_WATCH_TTL_SEC = int(os.getenv("DIALOGUE_WATCH_TTL_SEC", str(48 * 3600)))
DIALOGUE_THREAD_CONTEXT_LIMIT = int(os.getenv("DIALOGUE_THREAD_CONTEXT_LIMIT", "12"))
SESSION_LOCK_TTL_SEC = int(os.getenv("TG_SESSION_LOCK_TTL_SEC", "180"))

_client: Optional[redis.Redis] = None


def get_client() -> redis.Redis:
    """Lazy shared Redis client (decoded strings)."""
    global _client
    if _client is None:
        _client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=0,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=15,
            retry_on_timeout=True,
        )
    return _client


# ── Watch registry ─────────────────────────────────────────────────────────

def register_watch(
    *,
    agent_id: str,
    channel_ref,
    post_id: int,
    disc_chat_id: int,
    bot_msg_id: int,
    post_context: str = "",
    narrative_goal: str = "",
    tactic: str = "soft_support",
    role: str = "alpha",
    depth: int = 0,
    opponent_id: Optional[str] = None,
    mission_id: Optional[int] = None,
) -> Optional[str]:
    """
    Start listening on a comment the bot just posted. Returns the watch_id, or
    None on failure (best-effort — a registry miss must never break posting).
    """
    watch_id = f"{agent_id}:{disc_chat_id}:{bot_msg_id}"
    now = int(time.time())
    record = {
        "watch_id": watch_id,
        "agent_id": agent_id,
        "channel_ref": channel_ref,
        "post_id": post_id,
        "disc_chat_id": disc_chat_id,
        "bot_msg_id": bot_msg_id,
        # Stage 42 — so a reply the bot writes in this conversation is attributable to
        # the mission that started it, like every other action.
        "mission_id": mission_id,
        "post_context": (post_context or "")[:600],
        "narrative_goal": narrative_goal or "",
        "tactic": tactic or "soft_support",
        "role": role or "alpha",
        "depth": depth,
        "opponent_id": opponent_id,
        "last_seen_reply_id": 0,
        "created_at": now,
        "updated_at": now,
    }
    try:
        get_client().hset(WATCHES_KEY, watch_id, json.dumps(record, ensure_ascii=False))
        logger.info(
            "Dialogue watch registered: agent=%s post=%s bot_msg=%s depth=%d.",
            agent_id, post_id, bot_msg_id, depth,
        )
        return watch_id
    except Exception as exc:
        logger.error("Failed to register dialogue watch: %s", exc)
        return None


def list_watches() -> list[dict]:
    """All live watches, pruning any older than the TTL as a side effect."""
    try:
        raw = get_client().hgetall(WATCHES_KEY)
    except Exception as exc:
        logger.error("Failed to read dialogue watches: %s", exc)
        return []

    now = int(time.time())
    out: list[dict] = []
    for watch_id, blob in raw.items():
        try:
            rec = json.loads(blob)
        except json.JSONDecodeError:
            remove_watch(watch_id)
            continue
        if now - rec.get("created_at", now) > DIALOGUE_WATCH_TTL_SEC:
            remove_watch(watch_id)
            continue
        out.append(rec)
    return out


def update_watch(watch_id: str, **fields) -> None:
    try:
        client = get_client()
        blob = client.hget(WATCHES_KEY, watch_id)
        if not blob:
            return
        rec = json.loads(blob)
        rec.update(fields)
        rec["updated_at"] = int(time.time())
        client.hset(WATCHES_KEY, watch_id, json.dumps(rec, ensure_ascii=False))
    except Exception as exc:
        logger.error("Failed to update dialogue watch %s: %s", watch_id, exc)


def remove_watch(watch_id: str) -> None:
    try:
        get_client().hdel(WATCHES_KEY, watch_id)
    except Exception as exc:
        logger.error("Failed to remove dialogue watch %s: %s", watch_id, exc)


# ── Handled-reply dedupe ───────────────────────────────────────────────────

def is_handled(disc_chat_id: int, msg_id: int) -> bool:
    try:
        return bool(get_client().sismember(HANDLED_KEY, f"{disc_chat_id}:{msg_id}"))
    except Exception:
        return False


def mark_handled(disc_chat_id: int, msg_id: int) -> None:
    try:
        get_client().sadd(HANDLED_KEY, f"{disc_chat_id}:{msg_id}")
    except Exception as exc:
        logger.error("Failed to mark reply handled: %s", exc)


# ── Per-agent session advisory lock ────────────────────────────────────────

def acquire_session_lock(agent_id: str, ttl: int = SESSION_LOCK_TTL_SEC) -> Optional[str]:
    """
    Try to claim exclusive use of an agent's Telegram session. Returns an opaque
    token on success (pass it back to ``release_session_lock``), or None if the
    session is already in use elsewhere. Auto-expires after ``ttl`` so a crashed
    holder can never deadlock the session.
    """
    token = uuid.uuid4().hex
    try:
        ok = get_client().set(f"{SESSION_LOCK_PREFIX}{agent_id}", token, nx=True, ex=ttl)
        return token if ok else None
    except Exception as exc:
        logger.error("Session lock acquire failed for %s: %s", agent_id, exc)
        return None


def release_session_lock(agent_id: str, token: Optional[str]) -> None:
    """Release the lock only if we still own it (token match) — never steal."""
    if not token:
        return
    key = f"{SESSION_LOCK_PREFIX}{agent_id}"
    try:
        client = get_client()
        # Compare-and-delete so we don't release a lock that already expired and
        # was re-acquired by someone else.
        if client.get(key) == token:
            client.delete(key)
    except Exception as exc:
        logger.error("Session lock release failed for %s: %s", agent_id, exc)
