"""
MYRMIDON — Account Health & Telegram Limit Handling
======================================================
Keeps the swarm safe against Telegram's defenses:

  - FloodWait: short waits are slept-through and retried by the driver; long ones
    put the agent on a Redis cooldown so we back off instead of hammering.
  - PeerFlood (soft spam ban): the agent goes on a long cooldown (auto-recovers).
  - Fatal session death (UserDeactivated / AuthKeyUnregistered / SessionRevoked /
    SessionExpired): the account is marked ``banned`` in Postgres (status filter
    drops it from the active pool) and its profile is suspended.

Cooldowns are advisory: the engines (execute loop, target, dialogue) check
``in_cooldown`` before driving a session, so a limited agent simply goes quiet
until the window passes — no status change needed (auto-recovery).
"""

import logging
import os
from typing import Optional

import redis

from app.telemetry import emit as emit_event

logger = logging.getLogger("myrmidon.account_health")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
COOLDOWN_PREFIX = "morpheus:tg_cooldown:"

# Exception class names (matched by name to stay version-robust across Pyrogram).
FATAL_ERRORS = {
    "UserDeactivated", "UserDeactivatedBan", "AuthKeyUnregistered",
    "SessionRevoked", "SessionExpired", "Unauthorized", "AuthKeyDuplicated",
}
CHAT_LEVEL_ERRORS = {"UserBannedInChannel", "ChannelPrivate", "ChatWriteForbidden", "ChatAdminRequired"}

_sf = None
_redis: Optional[redis.Redis] = None


def _get_sf():
    global _sf
    if _sf is None:
        from app.main import connect_postgres
        _sf = connect_postgres()
    return _sf


def _get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True,
                             socket_connect_timeout=5, socket_timeout=15)
    return _redis


def set_cooldown(agent_id: str, seconds: int, reason: str = "") -> None:
    try:
        _get_redis().set(f"{COOLDOWN_PREFIX}{agent_id}", reason or "cooldown", ex=max(5, int(seconds)))
        logger.warning("account_health: agent %s on cooldown %ss (%s)", agent_id, int(seconds), reason)
        emit_event(agent_id, "cooldown", f"пауза {int(seconds)}с — лимит Telegram ({reason})",
                   status="warn", target="telegram")
    except Exception as exc:
        logger.error("account_health: failed to set cooldown: %s", exc)


def in_cooldown(agent_id: str) -> Optional[int]:
    """Remaining cooldown seconds, or None if the agent is free to act."""
    try:
        ttl = _get_redis().ttl(f"{COOLDOWN_PREFIX}{agent_id}")
        return ttl if ttl and ttl > 0 else None
    except Exception:
        return None


def mark_account(agent_id: str, status: str, reason: str = "") -> None:
    """Persist an account status (e.g. 'banned'); suspends the profile when banned."""
    from sqlalchemy import text
    s = _get_sf()()
    try:
        s.execute(text("UPDATE souls_accounts SET status=:st WHERE agent_id=:a AND platform='telegram'"),
                  {"st": status, "a": agent_id})
        if status in ("banned", "disabled"):
            s.execute(text("UPDATE agent_profiles SET status='suspended' WHERE agent_id=:a"), {"a": agent_id})
        s.commit()
        logger.error("account_health: agent %s account → %s (%s)", agent_id, status, reason)
    except Exception as exc:
        logger.error("account_health: failed to mark account: %s", exc)
        s.rollback()
    finally:
        s.close()
    emit_event(agent_id, "account_alert", f"аккаунт: {status} ({reason})",
               status="error", target="telegram")


def handle_fault(agent_id: str, exc: Exception) -> str:
    """
    Classify a Telegram error and react. Returns one of:
      'fatal'      — account banned/dead → marked banned (dropped from pool)
      'peer_flood' — soft spam ban → long cooldown (auto-recovers)
      'chat'       — per-chat block → caller should skip that chat only
      'other'      — nothing special
    """
    name = type(exc).__name__
    if name in FATAL_ERRORS:
        mark_account(agent_id, "banned", name)
        return "fatal"
    if name == "PeerFlood" or "PeerFlood" in name:
        set_cooldown(agent_id, 3600, "PeerFlood")
        return "peer_flood"
    if name in CHAT_LEVEL_ERRORS:
        return "chat"
    return "other"
