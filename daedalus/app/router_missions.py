"""
DAEDALUS — Mission Deck Router (Stage 34 — permanent-goal missions)
====================================================================
A Mission is a PERMANENT goal: it carries a stance ("truth"/side), a narrative
goal, a roster of agents (manual or dynamic), and many targets (channels/posts).
It is never "completed" — only ``active`` (in-progress) or ``paused``. Agents work
its targets continuously and may PROPOSE new targets (status ``suggested``) for the
operator to approve/reject. Per-post tactic is chosen dynamically at runtime.
"""

import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AdminUser, Mission, MissionSquad, MissionTarget, AgentProfile
from app.rbac import require_permission
from app import mission_control

logger = logging.getLogger("daedalus.router_missions")

INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "morpheus-internal-sync-key")

router = APIRouter(prefix="/api/v1/missions", tags=["Mission Deck"])

VALID_ROLES = ("alpha", "beta", "gamma")
VALID_TACTICS = ("soft_support", "aggressive_displacement", "dynamic")
VALID_STATUS = ("active", "paused")
VALID_KIND = ("channel", "post")
VALID_TARGET_STATUS = ("active", "suggested", "rejected")


# ── Schemas ───────────────────────────────────────────────────────────────

class SquadMemberRequest(BaseModel):
    agent_id: str
    assigned_role: str = Field("alpha", description="alpha | beta | gamma")


class TargetRequest(BaseModel):
    identifier: str = Field(..., min_length=1, description="@username, t.me url, or chat_id")
    kind: str = Field("channel", description="channel | post")
    title: Optional[str] = None


class MissionCreateRequest(BaseModel):
    title: str = Field(..., min_length=1)
    narrative_goal: Optional[str] = None
    stance: Optional[str] = None
    tactic: str = Field("dynamic", description="dynamic | soft_support | aggressive_displacement")
    forced_context: Optional[str] = None
    agent_mode: str = Field("manual", description="manual | dynamic")
    dynamic_count: int = Field(3, ge=0, le=50)
    squad: list[SquadMemberRequest] = Field(default_factory=list)
    targets: list[TargetRequest] = Field(default_factory=list)


class MissionUpdateRequest(BaseModel):
    title: Optional[str] = None
    narrative_goal: Optional[str] = None
    stance: Optional[str] = None
    tactic: Optional[str] = None
    forced_context: Optional[str] = None
    agent_mode: Optional[str] = None
    dynamic_count: Optional[int] = None


class StatusRequest(BaseModel):
    status: str = Field(..., description="active | paused")


class SquadMemberResponse(BaseModel):
    id: int
    agent_id: str
    assigned_role: str
    status: str
    codename: Optional[str] = None

    class Config:
        from_attributes = True


class TargetResponse(BaseModel):
    id: int
    kind: str
    identifier: str
    title: Optional[str]
    status: str
    source: str
    proposed_by: Optional[str]
    reason: Optional[str]
    created_at: Any

    class Config:
        from_attributes = True


class MissionResponse(BaseModel):
    id: int
    title: str
    platform: str
    narrative_goal: Optional[str]
    stance: Optional[str]
    tactic: str
    status: str
    agent_mode: str
    dynamic_count: int
    forced_context: Optional[str]
    created_at: Any
    squad: list[SquadMemberResponse]
    targets: list[TargetResponse]
    summary: dict[str, Any]

    class Config:
        from_attributes = True


# ── Helpers ────────────────────────────────────────────────────────────────

def _validate(value: str, allowed: tuple, what: str) -> None:
    if value not in allowed:
        raise HTTPException(status_code=400, detail=f"Invalid {what}. Allowed: {list(allowed)}")


def _summary(mission: Mission) -> dict[str, Any]:
    roles = {r: 0 for r in VALID_ROLES}
    for s in mission.squad:
        roles[s.assigned_role] = roles.get(s.assigned_role, 0) + 1
    tstat = {"active": 0, "suggested": 0, "rejected": 0}
    for t in mission.targets:
        tstat[t.status] = tstat.get(t.status, 0) + 1
    return {
        "status_label": "В процессе" if mission.status == "active" else "На паузе",
        "agents": {**roles, "total": len(mission.squad)},
        "targets": tstat,
    }


def _serialize(mission: Mission, db: Optional[Session] = None) -> MissionResponse:
    codenames: dict[str, str] = {}
    if db is not None and mission.squad:
        agent_ids = [s.agent_id for s in mission.squad]
        for agent_id, codename in (
            db.query(AgentProfile.agent_id, AgentProfile.codename)
            .filter(AgentProfile.agent_id.in_(agent_ids)).all()
        ):
            codenames[agent_id] = codename

    squad = []
    for s in mission.squad:
        m = SquadMemberResponse.model_validate(s)
        m.codename = codenames.get(s.agent_id)
        squad.append(m)

    targets = sorted(
        (TargetResponse.model_validate(t) for t in mission.targets),
        key=lambda t: (t.status != "suggested", t.id),  # suggested first
    )
    return MissionResponse(
        id=mission.id, title=mission.title, platform=mission.platform,
        narrative_goal=mission.narrative_goal, stance=mission.stance,
        tactic=mission.tactic, status=mission.status, agent_mode=mission.agent_mode,
        dynamic_count=mission.dynamic_count, forced_context=mission.forced_context,
        created_at=mission.created_at, squad=squad, targets=targets,
        summary=_summary(mission),
    )


# ── CRUD ───────────────────────────────────────────────────────────────────

@router.get("", response_model=list[MissionResponse])
def list_missions(
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("campaigns:view")),
) -> list[MissionResponse]:
    missions = db.query(Mission).order_by(Mission.id.desc()).all()
    return [_serialize(m, db) for m in missions]


@router.get("/{mission_id}", response_model=MissionResponse)
def get_mission(
    mission_id: int,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("campaigns:view")),
) -> MissionResponse:
    mission = db.query(Mission).filter(Mission.id == mission_id).first()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    return _serialize(mission, db)


@router.post("", response_model=MissionResponse, status_code=201)
def create_mission(
    request: MissionCreateRequest,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("campaigns:create")),
) -> MissionResponse:
    _validate(request.tactic, VALID_TACTICS, "tactic")
    _validate(request.agent_mode, ("manual", "dynamic"), "agent_mode")
    for m in request.squad:
        _validate(m.assigned_role, VALID_ROLES, "role")
    for t in request.targets:
        _validate(t.kind, VALID_KIND, "kind")

    mission = Mission(
        title=request.title,
        platform="telegram",
        narrative_goal=request.narrative_goal,
        stance=request.stance,
        tactic=request.tactic,
        forced_context=request.forced_context,
        agent_mode=request.agent_mode,
        dynamic_count=request.dynamic_count,
        status="active",
    )
    for m in request.squad:
        mission.squad.append(MissionSquad(agent_id=m.agent_id, assigned_role=m.assigned_role, status="active"))
    for t in request.targets:
        mission.targets.append(MissionTarget(
            kind=t.kind, identifier=t.identifier.strip(), title=t.title,
            status="active", source="operator"))

    db.add(mission)
    db.commit()
    db.refresh(mission)
    logger.info("Mission %s created: '%s' (%d agents, %d targets).",
                mission.id, mission.title, len(mission.squad), len(mission.targets))
    return _serialize(mission, db)


@router.put("/{mission_id}", response_model=MissionResponse)
def update_mission(
    mission_id: int,
    request: MissionUpdateRequest,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("campaigns:edit")),
) -> MissionResponse:
    mission = db.query(Mission).filter(Mission.id == mission_id).first()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    if request.tactic is not None:
        _validate(request.tactic, VALID_TACTICS, "tactic")
        mission.tactic = request.tactic
    if request.agent_mode is not None:
        _validate(request.agent_mode, ("manual", "dynamic"), "agent_mode")
        mission.agent_mode = request.agent_mode
    if request.title is not None:
        mission.title = request.title
    if request.narrative_goal is not None:
        mission.narrative_goal = request.narrative_goal
    if request.stance is not None:
        mission.stance = request.stance
    if request.forced_context is not None:
        mission.forced_context = request.forced_context
    if request.dynamic_count is not None:
        mission.dynamic_count = max(0, min(50, request.dynamic_count))
    db.commit()
    db.refresh(mission)
    return _serialize(mission, db)


@router.post("/{mission_id}/status", response_model=MissionResponse)
def set_status(
    mission_id: int,
    request: StatusRequest,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("campaigns:edit")),
) -> MissionResponse:
    """Pause or resume a permanent mission (active ↔ paused)."""
    _validate(request.status, VALID_STATUS, "status")
    mission = db.query(Mission).filter(Mission.id == mission_id).first()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    mission.status = request.status
    db.commit()
    db.refresh(mission)
    return _serialize(mission, db)


@router.delete("/{mission_id}")
def delete_mission(
    mission_id: int,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("campaigns:delete")),
) -> dict[str, str]:
    mission = db.query(Mission).filter(Mission.id == mission_id).first()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    db.delete(mission)
    db.commit()
    return {"status": "success", "message": f"Mission {mission_id} deleted."}


# ── Targets ──────────────────────────────────────────────────────────────────

@router.post("/{mission_id}/targets", response_model=MissionResponse, status_code=201)
def add_target(
    mission_id: int,
    request: TargetRequest,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("campaigns:edit")),
) -> MissionResponse:
    _validate(request.kind, VALID_KIND, "kind")
    mission = db.query(Mission).filter(Mission.id == mission_id).first()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    ident = request.identifier.strip()
    if any(t.identifier == ident for t in mission.targets):
        raise HTTPException(status_code=409, detail="Target already in this mission.")
    db.add(MissionTarget(mission_id=mission_id, kind=request.kind, identifier=ident,
                         title=request.title, status="active", source="operator"))
    db.commit()
    db.refresh(mission)
    return _serialize(mission, db)


@router.post("/{mission_id}/targets/{target_id}/{decision}", response_model=MissionResponse)
def decide_target(
    mission_id: int,
    target_id: int,
    decision: str,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("campaigns:edit")),
) -> MissionResponse:
    """Approve or reject an agent-suggested target (decision = approve | reject)."""
    if decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="decision must be approve | reject")
    mission = db.query(Mission).filter(Mission.id == mission_id).first()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    t = db.query(MissionTarget).filter(
        MissionTarget.id == target_id, MissionTarget.mission_id == mission_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Target not found")
    t.status = "active" if decision == "approve" else "rejected"
    db.commit()
    db.refresh(mission)
    return _serialize(mission, db)


@router.delete("/{mission_id}/targets/{target_id}", response_model=MissionResponse)
def delete_target(
    mission_id: int,
    target_id: int,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("campaigns:edit")),
) -> MissionResponse:
    mission = db.query(Mission).filter(Mission.id == mission_id).first()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    t = db.query(MissionTarget).filter(
        MissionTarget.id == target_id, MissionTarget.mission_id == mission_id).first()
    if t:
        db.delete(t)
        db.commit()
    db.refresh(mission)
    return _serialize(mission, db)


# ── Agent-proposed targets (internal: called by MYRMIDON) ────────────────────

class SuggestTargetRequest(BaseModel):
    mission_id: int
    identifier: str = Field(..., min_length=1, description="@username / t.me url / chat_id")
    kind: str = Field("channel", description="channel | post")
    title: Optional[str] = None
    proposed_by: Optional[str] = Field(None, description="agent_id that found it")
    reason: Optional[str] = Field(None, description="why it's relevant (e.g. a post snippet)")


@router.post("/internal/suggest-target")
def suggest_target(
    request: SuggestTargetRequest,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    An agent (any caste, via MYRMIDON) proposes a channel/post it reads as a new
    target for an active mission. Stored as ``status='suggested', source='agent'``
    for the operator to approve/reject in the Mission Deck. Idempotent: if the
    identifier already exists on the mission (any status — including a prior
    ``rejected``), it is left untouched so agents never re-spam a declined target.
    """
    if x_internal_token != INTERNAL_API_TOKEN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid internal token.")
    if request.kind not in VALID_KIND:
        raise HTTPException(status_code=400, detail=f"Invalid kind. Allowed: {list(VALID_KIND)}")

    mission = db.query(Mission).filter(Mission.id == request.mission_id).first()
    if not mission:
        return {"status": "skipped", "reason": "mission_not_found"}
    if mission.status != "active":
        return {"status": "skipped", "reason": "mission_not_active"}

    ident = request.identifier.strip()
    existing = db.query(MissionTarget).filter(
        MissionTarget.mission_id == request.mission_id,
        MissionTarget.identifier == ident,
    ).first()
    if existing:
        return {"status": "exists", "target_status": existing.status}

    target = MissionTarget(
        mission_id=request.mission_id, kind=request.kind, identifier=ident,
        title=request.title, status="suggested", source="agent",
        proposed_by=request.proposed_by, reason=request.reason,
    )
    db.add(target)
    db.commit()
    db.refresh(target)
    logger.info("Mission %s — agent %s suggested target '%s' (target %s).",
                request.mission_id, request.proposed_by, ident, target.id)
    return {"status": "created", "target_id": target.id}


# ── Squad management ─────────────────────────────────────────────────────────

@router.post("/{mission_id}/squad", response_model=MissionResponse, status_code=201)
def add_squad_member(
    mission_id: int,
    member: SquadMemberRequest,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("campaigns:edit")),
) -> MissionResponse:
    _validate(member.assigned_role, VALID_ROLES, "role")
    mission = db.query(Mission).filter(Mission.id == mission_id).first()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    exists = db.query(MissionSquad).filter(
        MissionSquad.mission_id == mission_id, MissionSquad.agent_id == member.agent_id).first()
    if exists:
        raise HTTPException(status_code=409, detail=f"Agent '{member.agent_id}' already enlisted.")
    load = mission_control.agent_active_mission_count(db, member.agent_id, exclude_mission_id=mission_id)
    if load >= mission_control.MAX_MISSIONS_PER_BOT:
        raise HTTPException(status_code=409,
                            detail=f"Agent '{member.agent_id}' at mission cap ({mission_control.MAX_MISSIONS_PER_BOT}).")
    db.add(MissionSquad(mission_id=mission_id, agent_id=member.agent_id,
                        assigned_role=member.assigned_role, status="active"))
    db.commit()
    db.refresh(mission)
    return _serialize(mission, db)


@router.delete("/{mission_id}/squad/{squad_id}", response_model=MissionResponse)
def remove_squad_member(
    mission_id: int,
    squad_id: int,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("campaigns:edit")),
) -> MissionResponse:
    mission = db.query(Mission).filter(Mission.id == mission_id).first()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    member = db.query(MissionSquad).filter(
        MissionSquad.id == squad_id, MissionSquad.mission_id == mission_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Squad member not found")
    db.delete(member)
    db.commit()
    db.refresh(mission)
    return _serialize(mission, db)


class EligibleAgentResponse(BaseModel):
    agent_id: str
    codename: Optional[str]
    caste: str
    status: str
    platform: str
    active_mission_load: int
    at_capacity: bool
    already_enlisted: bool
    match_score: float
    match_reasons: list[str]


class AutoAssignRequest(BaseModel):
    alpha: int = Field(1, ge=0, le=20)
    beta: int = Field(0, ge=0, le=20)
    gamma: int = Field(0, ge=0, le=20)


@router.get("/{mission_id}/eligible-agents", response_model=list[EligibleAgentResponse])
def list_eligible_agents(
    mission_id: int,
    role: Optional[str] = None,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("campaigns:view")),
) -> list[EligibleAgentResponse]:
    mission = db.query(Mission).filter(Mission.id == mission_id).first()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    if role is not None:
        _validate(role, VALID_ROLES, "role")
    candidates = mission_control.eligible_agents_for_mission(db, mission, role=role, include_enlisted=True)
    return [EligibleAgentResponse(**c) for c in candidates]


@router.post("/{mission_id}/auto-assign", response_model=MissionResponse)
def auto_assign_squad(
    mission_id: int,
    request: AutoAssignRequest,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("campaigns:edit")),
) -> MissionResponse:
    mission = db.query(Mission).filter(Mission.id == mission_id).first()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    report = mission_control.auto_assign_squad(
        db, mission, {"alpha": request.alpha, "beta": request.beta, "gamma": request.gamma})
    db.refresh(mission)
    logger.info("Mission %s auto-assign: %d added, %d at cap.",
                mission_id, report["assigned_count"], report["skipped_at_capacity"])
    return _serialize(mission, db)
