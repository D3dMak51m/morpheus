"""
DAEDALUS — Scouting Radar Router (Stage 18)
=============================================
Receives viral discoveries from HUGINN's authenticated scouting engine,
serves them to the operator's Scouting Radar, and converts a hot target into
a Mission draft with one click.

It also exposes an internal endpoint that hands HUGINN the authenticated
session cookies / proxies it needs to impersonate the mobile apps.
"""

import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AdminUser, ScoutedTarget, SoulAccount, Mission
from app.rbac import require_permission
from app import mission_control

logger = logging.getLogger("daedalus.router_scouting")

router = APIRouter(prefix="/api/v1/scouting", tags=["Scouting Radar"])

INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "morpheus-internal-sync-key")

# Platforms whose authenticated sessions HUGINN may use for scouting.
SCOUTING_PLATFORMS = ("instagram", "twitter", "x", "threads", "telegram")


# ── Schemas ───────────────────────────────────────────────────────────────

class HotTargetRequest(BaseModel):
    platform: str
    url: str
    author_name: Optional[str] = None
    content_summary: Optional[str] = None
    velocity_score: float = Field(0.0, ge=0)
    engagement: int = Field(0, ge=0)
    posted_at: Optional[int] = None


class ScoutedTargetResponse(BaseModel):
    id: int
    platform: str
    url: str
    author_name: Optional[str]
    content_summary: Optional[str]
    velocity_score: float
    engagement: int
    posted_at: Optional[int]
    status: str
    mission_id: Optional[int]
    created_at: Any

    class Config:
        from_attributes = True


class SessionResponse(BaseModel):
    agent_id: Optional[str]
    platform: str
    username: str
    auth_cookies: Optional[dict]
    assigned_proxy: Optional[str]


# ── Internal: HUGINN pushes viral discoveries ────────────────────────────

@router.post("/hot-targets")
def push_hot_target(
    request: HotTargetRequest,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Internal endpoint for HUGINN to register a discovered viral target."""
    if x_internal_token != INTERNAL_API_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid internal token")

    existing = db.query(ScoutedTarget).filter(ScoutedTarget.url == request.url).first()
    if existing:
        # Refresh velocity on a re-discovery; never resurrect a handled target.
        if existing.status == "pending":
            existing.velocity_score = request.velocity_score
            existing.engagement = request.engagement
            db.commit()
        return {"status": "updated", "id": existing.id}

    target = ScoutedTarget(
        platform=request.platform,
        url=request.url,
        author_name=request.author_name,
        content_summary=request.content_summary,
        velocity_score=request.velocity_score,
        engagement=request.engagement,
        posted_at=request.posted_at,
        status="pending",
    )
    db.add(target)
    db.commit()
    db.refresh(target)
    logger.info(
        "Scouted target registered: %s (velocity=%.1f) on %s",
        request.url, request.velocity_score, request.platform,
    )
    return {"status": "created", "id": target.id}


# ── Internal: HUGINN fetches authenticated sessions ──────────────────────

@router.get("/internal/sessions")
def list_scouting_sessions(
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Hand HUGINN the active social sessions (cookies + proxy) it should use to
    impersonate the mobile apps. Secured by the internal token.
    """
    if x_internal_token != INTERNAL_API_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid internal token")

    accounts = (
        db.query(SoulAccount)
        .filter(
            SoulAccount.platform.in_(SCOUTING_PLATFORMS),
            SoulAccount.status == "active",
            SoulAccount.auth_cookies.isnot(None),
        )
        .all()
    )
    sessions = [
        SessionResponse(
            agent_id=a.agent_id,
            platform=a.platform,
            username=a.username,
            auth_cookies=a.auth_cookies,
            assigned_proxy=a.assigned_proxy,
        ).model_dump()
        for a in accounts
    ]
    return {"sessions": sessions, "total": len(sessions)}


# ── Operator: Scouting Radar ──────────────────────────────────────────────

@router.get("/radar", response_model=list[ScoutedTargetResponse])
def get_radar(
    include_handled: bool = False,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("monitoring:view")),
) -> list[ScoutedTargetResponse]:
    """Return scouted targets for the radar, hottest first."""
    query = db.query(ScoutedTarget)
    if not include_handled:
        query = query.filter(ScoutedTarget.status == "pending")
    targets = query.order_by(ScoutedTarget.velocity_score.desc(), ScoutedTarget.id.desc()).all()
    return [ScoutedTargetResponse.model_validate(t) for t in targets]


@router.post("/{target_id}/dismiss", response_model=ScoutedTargetResponse)
def dismiss_target(
    target_id: int,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("monitoring:view")),
) -> ScoutedTargetResponse:
    """Hide a scouted target from the radar."""
    target = db.query(ScoutedTarget).filter(ScoutedTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Scouted target not found")
    target.status = "dismissed"
    db.commit()
    db.refresh(target)
    return ScoutedTargetResponse.model_validate(target)


class ConvertResponse(BaseModel):
    status: str
    mission_id: int
    target_url: str
    title: str


@router.post("/{target_id}/convert", response_model=ConvertResponse)
def convert_to_mission(
    target_id: int,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("campaigns:create")),
) -> ConvertResponse:
    """
    Convert a hot target into a Mission draft. Provisions a pending Mission
    (no squad yet) seeded from the scouted post, links it back to the target,
    and flips the target's status to 'converted_to_mission'.
    """
    target = db.query(ScoutedTarget).filter(ScoutedTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Scouted target not found")
    if target.status == "converted_to_mission" and target.mission_id:
        raise HTTPException(status_code=409, detail="Target already converted to a mission")

    author = target.author_name or "unknown source"
    title = f"Counter: {author} viral post"[:200]
    goal = (
        target.content_summary
        or f"Engage the viral discussion around {author}'s post on {target.platform}."
    )

    mission = Mission(
        title=title,
        target_url=target.url,
        platform=mission_control.infer_platform(target.url) or target.platform,
        narrative_goal=goal,
        tactic="soft_support",
        status="pending",
    )
    db.add(mission)
    db.flush()  # obtain mission.id

    target.status = "converted_to_mission"
    target.mission_id = mission.id
    db.commit()

    logger.info("Scouted target %s converted → Mission %s.", target_id, mission.id)
    return ConvertResponse(
        status="converted",
        mission_id=mission.id,
        target_url=mission.target_url,
        title=mission.title,
    )
