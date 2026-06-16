"""
MYRMIDON — Active-hours gate
==============================
A persona is only "awake" during its active window (``agent_profiles.active_hours_start``
/ ``…_end``, 0-23). Outside it the bot must NOT post (seed comments, amplify, or reply) —
so the swarm reads like real people with daily rhythms instead of acting 24/7. Read-only
work (news ingest, channel profiling) is NOT gated.

Hours are interpreted in the swarm's operating timezone via a fixed UTC offset
(``ACTIVE_HOURS_UTC_OFFSET``, default 5 = Asia/Tashkent) since personas carry no per-agent
timezone. Fail-open: on any DB/parse error we treat the agent as awake (a glitch must not
silence the whole swarm).
"""

import datetime
import logging
import os

from sqlalchemy import text

logger = logging.getLogger("myrmidon.schedule")

ACTIVE_HOURS_UTC_OFFSET = int(os.getenv("ACTIVE_HOURS_UTC_OFFSET", "5"))  # Tashkent


def local_hour() -> int:
    return (datetime.datetime.utcnow().hour + ACTIVE_HOURS_UTC_OFFSET) % 24


def _within(start: int, end: int, h: int) -> bool:
    if start == end:
        return True                      # start==end → always on (24h)
    if start < end:
        return start <= h < end          # same-day window, e.g. 8..22
    return h >= start or h < end         # overnight window, e.g. 22..6


def in_active_hours(sf, agent_id: str) -> bool:
    """True if ``agent_id``'s persona is currently in its active window (fail-open)."""
    try:
        session = sf()
        try:
            row = session.execute(
                text("SELECT active_hours_start, active_hours_end FROM agent_profiles "
                     "WHERE agent_id=:a LIMIT 1"),
                {"a": agent_id},
            ).fetchone()
        finally:
            session.close()
    except Exception as exc:
        logger.debug("active-hours check failed for %s: %s", agent_id, exc)
        return True
    if not row:
        return True
    try:
        return _within(int(row[0]), int(row[1]), local_hour())
    except (TypeError, ValueError):
        return True
