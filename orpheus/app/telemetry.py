"""
ORPHEUS — Live Telemetry Emitter
==================================
Fire-and-forget activity events for the Live Ops timeline (memory search/write,
"thinking", reading news, guardrail rejections). XADDed to a capped Redis stream
that DAEDALUS tails. Best-effort: never raises, never blocks inference.
"""

import logging
import os
import time
from typing import Optional

import redis

logger = logging.getLogger("orpheus.telemetry")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

EVENTS_STREAM = "stream:agent_events"
STREAM_MAXLEN = int(os.getenv("AGENT_EVENTS_MAXLEN", "3000"))
SERVICE = "orpheus"

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
    """Publish one activity event (see myrmidon.telemetry for the contract)."""
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
