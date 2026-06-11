"""
DAEDALUS — Data Retention & Pruning Policy (Stage 19)
=======================================================
Swarm resilience: keeps PostgreSQL/Redis from OOM-crashing under 24/7
autonomous operation by periodically pruning stale rows.

Rules:
  1. Delete CapturedEvent (captured_raw_events) older than 48 hours.
  2. Delete ScoutedTarget rows with status 'dismissed' older than 24 hours.

The pruner runs as a repeating asyncio loop started from the FastAPI lifespan.
All blocking SQLAlchemy work is offloaded with `asyncio.to_thread` so the event
loop — and therefore the API request threads — are never blocked.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.database import SessionLocal
from app.models import CapturedEvent, ScoutedTarget

logger = logging.getLogger("daedalus.retention")

CAPTURED_EVENT_TTL_HOURS = 48
DISMISSED_TARGET_TTL_HOURS = 24
PRUNE_INTERVAL_SEC = 12 * 3600  # every 12 hours

# Last-run telemetry surfaced to the Swarm Overlord widget.
_last_run: Dict[str, Any] = {
    "last_run_at": None,
    "captured_pruned": 0,
    "targets_pruned": 0,
    "status": "idle",
}

_retention_task: Optional[asyncio.Task] = None
_stop = asyncio.Event()


def get_retention_status() -> Dict[str, Any]:
    """Return the most recent pruning telemetry (read by the dashboard)."""
    return dict(_last_run)


def prune_once() -> Dict[str, int]:
    """
    Execute both retention rules in a single transaction. Synchronous —
    intended to be invoked via `asyncio.to_thread`. Safe & idempotent.
    """
    now = datetime.now(timezone.utc)
    captured_cutoff = now - timedelta(hours=CAPTURED_EVENT_TTL_HOURS)
    dismissed_cutoff = now - timedelta(hours=DISMISSED_TARGET_TTL_HOURS)

    db = SessionLocal()
    try:
        captured_pruned = (
            db.query(CapturedEvent)
            .filter(CapturedEvent.created_at < captured_cutoff)
            .delete(synchronize_session=False)
        )
        targets_pruned = (
            db.query(ScoutedTarget)
            .filter(
                ScoutedTarget.status == "dismissed",
                ScoutedTarget.created_at < dismissed_cutoff,
            )
            .delete(synchronize_session=False)
        )
        db.commit()
        return {"captured_pruned": captured_pruned, "targets_pruned": targets_pruned}
    except Exception:
        db.rollback()
        logger.exception("Retention prune failed — rolled back.")
        return {"captured_pruned": 0, "targets_pruned": 0}
    finally:
        db.close()


async def _run_prune() -> None:
    result = await asyncio.to_thread(prune_once)
    _last_run.update({
        "last_run_at": datetime.now(timezone.utc).isoformat(),
        "captured_pruned": result["captured_pruned"],
        "targets_pruned": result["targets_pruned"],
        "status": "ok",
    })
    logger.info(
        "Retention sweep complete — pruned %d captured event(s), %d dismissed target(s).",
        result["captured_pruned"], result["targets_pruned"],
    )


async def retention_loop() -> None:
    """Repeating retention scheduler. Prunes once on boot, then every 12h."""
    logger.info("Data retention loop started (interval=%dh).", PRUNE_INTERVAL_SEC // 3600)
    _stop.clear()
    # Initial sweep shortly after boot (let the DB settle first).
    await asyncio.sleep(30)
    while not _stop.is_set():
        try:
            await _run_prune()
        except Exception:
            logger.exception("Retention loop tick failed.")
            _last_run["status"] = "error"
        try:
            await asyncio.wait_for(_stop.wait(), timeout=PRUNE_INTERVAL_SEC)
        except asyncio.TimeoutError:
            pass
    logger.info("Data retention loop stopped.")


def start_retention(loop: asyncio.AbstractEventLoop) -> None:
    """Schedule the retention loop on the running event loop (idempotent)."""
    global _retention_task
    if _retention_task and not _retention_task.done():
        return
    _retention_task = loop.create_task(retention_loop())
    logger.info("Retention scheduler task created.")


def stop_retention() -> None:
    """Signal the retention loop to stop."""
    _stop.set()
