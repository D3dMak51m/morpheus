"""
MYRMIDON — Live Telemetry Emitter
===================================
Fire-and-forget activity events for the Live Ops timeline. Every meaningful step
a bot takes (polling, reading a post, sensing the thread mood, posting a comment,
answering a human) is XADDed to a capped Redis stream that DAEDALUS tails and the
dashboard renders chronologically in near-real-time.

Deliberately best-effort: telemetry must NEVER slow down or break execution, so
all errors are swallowed. This is ephemeral signal, not the durable audit log
(that stays in Postgres ``agent_activity_logs``).
"""

import logging
import os
import time
from typing import Optional

import redis

logger = logging.getLogger("myrmidon.telemetry")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

EVENTS_STREAM = "stream:agent_events"
STREAM_MAXLEN = int(os.getenv("AGENT_EVENTS_MAXLEN", "3000"))
SERVICE = "myrmidon"

_client: Optional[redis.Redis] = None


def _get_client() -> Optional[redis.Redis]:
    global _client
    if _client is None:
        try:
            _client = redis.Redis(
                host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True,
                socket_connect_timeout=2, socket_timeout=2,
            )
        except Exception as exc:
            logger.debug("telemetry: redis client init failed: %s", exc)
            return None
    return _client


def emit(agent_id: str, event: str, detail: str = "", status: str = "info",
         target: str = "") -> None:
    """
    Publish one activity event.
      event   — machine key the UI maps to an icon/colour (poll, reading_post,
                reading_thread, posting, commented, replied, reply_detected, …)
      detail  — short human-readable text shown on the timeline
      status  — active | ok | warn | error | info  (drives colour / pulse)
      target  — optional url / channel / person the action is about
    """
    client = _get_client()
    if client is None:
        return
    try:
        client.xadd(
            EVENTS_STREAM,
            {
                "agent_id": agent_id or "unknown",
                "service": SERVICE,
                "event": event,
                "detail": detail or "",
                "status": status or "info",
                "target": target or "",
                "ts": f"{time.time():.3f}",
            },
            maxlen=STREAM_MAXLEN,
            approximate=True,
        )
    except Exception as exc:
        logger.debug("telemetry emit failed (%s/%s): %s", agent_id, event, exc)
