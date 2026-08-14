"""
DAEDALUS — Decision Log Router
================================
Durable record of the swarm's decision chain (what it recognized → relevance verdict →
action/skip), so the operator can see WHY a bot did or didn't react — not just the final
comment. The capped Live Ops stream shows these live; this keeps queryable history.

  POST /decisions/internal/log  — MYRMIDON logs a decision (X-Internal-Token)
  GET  /decisions               — operator: recent decisions (+ channel/kind filters)
"""

import logging
import os
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AdminUser, DecisionEvent
from app.rbac import require_permission

logger = logging.getLogger("daedalus.router_decisions")

INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "morpheus-internal-sync-key")
RETENTION_DAYS = int(os.getenv("DECISION_RETENTION_DAYS", "7"))

router = APIRouter(prefix="/api/v1/decisions", tags=["Decision Log"])


class DecisionLogRequest(BaseModel):
    agent_id: Optional[str] = None
    mission_id: Optional[int] = None
    platform: str = "telegram"
    channel_ref: Optional[str] = None
    post_url: Optional[str] = None
    kind: str = Field(..., description="relevance | skip | comment")
    detail: Optional[str] = None
    verdict: Optional[bool] = None


def _serialize(d: DecisionEvent) -> dict[str, Any]:
    return {
        "id": d.id, "agent_id": d.agent_id, "mission_id": d.mission_id,
        "platform": d.platform, "channel_ref": d.channel_ref, "post_url": d.post_url,
        "kind": d.kind, "detail": d.detail, "verdict": d.verdict,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }


@router.post("/internal/log")
def log_decision(
    request: DecisionLogRequest,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if x_internal_token != INTERNAL_API_TOKEN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid internal token.")
    row = DecisionEvent(
        agent_id=request.agent_id, mission_id=request.mission_id, platform=request.platform,
        channel_ref=request.channel_ref, post_url=request.post_url, kind=request.kind,
        detail=(request.detail or "")[:2000], verdict=request.verdict,
    )
    db.add(row)
    db.commit()
    # Opportunistic retention prune (cheap — only ~5% of writes).
    if random.random() < 0.05:
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
            db.execute(text("DELETE FROM decision_events WHERE created_at < :c"), {"c": cutoff})
            db.commit()
        except Exception:
            db.rollback()
    return {"status": "ok", "id": row.id}


@router.get("")
def list_decisions(
    channel_ref: Optional[str] = None,
    kind: Optional[str] = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("monitoring:view")),
) -> dict[str, Any]:
    """Recent decisions, newest first, with optional channel/kind filters."""
    q = db.query(DecisionEvent)
    if channel_ref:
        q = q.filter(DecisionEvent.channel_ref == channel_ref)
    if kind:
        q = q.filter(DecisionEvent.kind == kind)
    rows = q.order_by(DecisionEvent.id.desc()).limit(min(max(limit, 1), 500)).all()
    return {"decisions": [_serialize(d) for d in rows], "count": len(rows)}
