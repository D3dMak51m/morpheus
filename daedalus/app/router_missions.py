"""
DAEDALUS — Mission Deck Router (Stage 17)
==========================================
Full CRUD for coordinated multi-agent Missions plus the DAG launch trigger.

A Mission bundles a strategic narrative goal, a target, a tactic, and a Squad
of agents bound to Alpha / Beta / Gamma roles. Launching a mission hands it to
the Mission Control DAG engine, which sequences the waves through Redis.
"""

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AdminUser, Mission, MissionSquad
from app.rbac import require_permission
from app import mission_control

logger = logging.getLogger("daedalus.router_missions")

router = APIRouter(prefix="/api/v1/missions", tags=["Mission Deck"])

VALID_ROLES = ("alpha", "beta", "gamma")
VALID_TACTICS = ("soft_support", "aggressive_displacement")


# ── Schemas ───────────────────────────────────────────────────────────────

class SquadMemberRequest(BaseModel):
    agent_id: str
    assigned_role: str = Field("alpha", description="alpha | beta | gamma")


class MissionCreateRequest(BaseModel):
    title: str = Field(..., min_length=1)
    target_url: str = Field(..., min_length=1)
    narrative_goal: Optional[str] = None
    tactic: str = Field("soft_support", description="soft_support | aggressive_displacement")
    # Stage 21 — optional operator-pinned fact; bypasses ORPHEUS vector search.
    forced_context: Optional[str] = None
    squad: list[SquadMemberRequest] = Field(default_factory=list)


class MissionUpdateRequest(BaseModel):
    title: Optional[str] = None
    target_url: Optional[str] = None
    narrative_goal: Optional[str] = None
    tactic: Optional[str] = None
    forced_context: Optional[str] = None


class SquadMemberResponse(BaseModel):
    id: int
    agent_id: str
    assigned_role: str
    status: str

    class Config:
        from_attributes = True


class MissionResponse(BaseModel):
    id: int
    title: str
    target_url: str
    platform: str
    narrative_goal: Optional[str]
    tactic: str
    status: str
    alpha_context: Optional[str]
    forced_context: Optional[str]
    launched_at: Optional[Any]
    created_at: Any
    squad: list[SquadMemberResponse]
    progress: dict[str, Any]

    class Config:
        from_attributes = True


# ── Helpers ────────────────────────────────────────────────────────────────

def _validate_tactic(tactic: str) -> None:
    if tactic not in VALID_TACTICS:
        raise HTTPException(status_code=400, detail=f"Invalid tactic. Allowed: {list(VALID_TACTICS)}")


def _validate_role(role: str) -> None:
    if role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role. Allowed: {list(VALID_ROLES)}")


def _compute_progress(mission: Mission) -> dict[str, Any]:
    """Derive a DAG-state summary + human-readable stage label for the UI."""
    by_role: dict[str, dict[str, int]] = {r: {"total": 0, "success": 0, "failed": 0, "active": 0} for r in VALID_ROLES}
    for s in mission.squad:
        bucket = by_role.setdefault(s.assigned_role, {"total": 0, "success": 0, "failed": 0, "active": 0})
        bucket["total"] += 1
        if s.status == "success":
            bucket["success"] += 1
        elif s.status == "failed":
            bucket["failed"] += 1
        elif s.status in ("dispatched", "locked", "pending"):
            bucket["active"] += 1

    total = len(mission.squad)
    done = sum(1 for s in mission.squad if s.status in ("success", "failed"))
    pct = round((done / total) * 100) if total else 0

    if mission.status == "pending":
        stage = "Awaiting Launch"
    elif mission.status == "running":
        stage = "Alpha Deployed → Awaiting Success"
    elif mission.status == "amplifying":
        stage = "Alpha Secured → Betas & Gammas Amplifying"
    elif mission.status == "completed":
        stage = "Mission Complete"
    elif mission.status == "failed":
        stage = "Alpha Failed → Mission Aborted"
    else:
        stage = mission.status

    return {"percent": pct, "stage": stage, "done": done, "total": total, "by_role": by_role}


def _serialize(mission: Mission) -> MissionResponse:
    return MissionResponse(
        id=mission.id,
        title=mission.title,
        target_url=mission.target_url,
        platform=mission.platform,
        narrative_goal=mission.narrative_goal,
        tactic=mission.tactic,
        status=mission.status,
        alpha_context=mission.alpha_context,
        forced_context=mission.forced_context,
        launched_at=mission.launched_at,
        created_at=mission.created_at,
        squad=[SquadMemberResponse.model_validate(s) for s in mission.squad],
        progress=_compute_progress(mission),
    )


# ── CRUD ───────────────────────────────────────────────────────────────────

@router.get("", response_model=list[MissionResponse])
def list_missions(
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("campaigns:view")),
) -> list[MissionResponse]:
    """List all missions (newest first) with squad and live DAG progress."""
    missions = db.query(Mission).order_by(Mission.id.desc()).all()
    return [_serialize(m) for m in missions]


@router.get("/{mission_id}", response_model=MissionResponse)
def get_mission(
    mission_id: int,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("campaigns:view")),
) -> MissionResponse:
    mission = db.query(Mission).filter(Mission.id == mission_id).first()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    return _serialize(mission)


@router.post("", response_model=MissionResponse, status_code=201)
def create_mission(
    request: MissionCreateRequest,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("campaigns:create")),
) -> MissionResponse:
    """Create a mission and (optionally) assemble its initial squad."""
    _validate_tactic(request.tactic)
    for member in request.squad:
        _validate_role(member.assigned_role)

    mission = Mission(
        title=request.title,
        target_url=request.target_url,
        platform=mission_control.infer_platform(request.target_url),
        narrative_goal=request.narrative_goal,
        tactic=request.tactic,
        forced_context=request.forced_context,
        status="pending",
    )
    for member in request.squad:
        mission.squad.append(MissionSquad(agent_id=member.agent_id, assigned_role=member.assigned_role))

    db.add(mission)
    db.commit()
    db.refresh(mission)
    logger.info("Mission %s created: '%s' (%d squad members).", mission.id, mission.title, len(mission.squad))
    return _serialize(mission)


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
    if mission.status not in ("pending", "failed"):
        raise HTTPException(status_code=409, detail=f"Cannot edit a '{mission.status}' mission.")

    if request.tactic is not None:
        _validate_tactic(request.tactic)
        mission.tactic = request.tactic
    if request.title is not None:
        mission.title = request.title
    if request.target_url is not None:
        mission.target_url = request.target_url
        mission.platform = mission_control.infer_platform(request.target_url)
    if request.narrative_goal is not None:
        mission.narrative_goal = request.narrative_goal
    if request.forced_context is not None:
        mission.forced_context = request.forced_context

    db.commit()
    db.refresh(mission)
    return _serialize(mission)


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


# ── Squad management ─────────────────────────────────────────────────────────

@router.post("/{mission_id}/squad", response_model=MissionResponse, status_code=201)
def add_squad_member(
    mission_id: int,
    member: SquadMemberRequest,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("campaigns:edit")),
) -> MissionResponse:
    """Assign an agent to an Alpha/Beta/Gamma slot on a pending mission."""
    _validate_role(member.assigned_role)
    mission = db.query(Mission).filter(Mission.id == mission_id).first()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    if mission.status not in ("pending", "failed"):
        raise HTTPException(status_code=409, detail=f"Cannot modify squad of a '{mission.status}' mission.")

    exists = (
        db.query(MissionSquad)
        .filter(MissionSquad.mission_id == mission_id, MissionSquad.agent_id == member.agent_id)
        .first()
    )
    if exists:
        raise HTTPException(status_code=409, detail=f"Agent '{member.agent_id}' already enlisted in this mission.")

    db.add(MissionSquad(mission_id=mission_id, agent_id=member.agent_id, assigned_role=member.assigned_role))
    db.commit()
    db.refresh(mission)
    return _serialize(mission)


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
    if mission.status not in ("pending", "failed"):
        raise HTTPException(status_code=409, detail=f"Cannot modify squad of a '{mission.status}' mission.")

    member = (
        db.query(MissionSquad)
        .filter(MissionSquad.id == squad_id, MissionSquad.mission_id == mission_id)
        .first()
    )
    if not member:
        raise HTTPException(status_code=404, detail="Squad member not found")
    db.delete(member)
    db.commit()
    db.refresh(mission)
    return _serialize(mission)


# ── DAG launch ───────────────────────────────────────────────────────────────

@router.post("/{mission_id}/launch", response_model=MissionResponse)
def launch_mission(
    mission_id: int,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("campaigns:create")),
) -> MissionResponse:
    """
    Trigger the DAG: dispatch the Alpha wave to Redis and lock downstream
    waves until Alpha reports SUCCESS (handled by the reconciler).
    """
    mission = db.query(Mission).filter(Mission.id == mission_id).first()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    try:
        mission_control.launch_mission(db, mission)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Mission %s launch failed.", mission_id)
        raise HTTPException(status_code=502, detail=f"Failed to dispatch mission to Redis: {e}")
    db.refresh(mission)
    return _serialize(mission)


# ── Internal callback (optional fast-path for the DAG reconciler) ───────────

class MissionCallbackRequest(BaseModel):
    agent_id: str
    target_url: str
    status: str


@router.post("/internal/callback")
def mission_internal_callback(
    request: MissionCallbackRequest,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """
    Optional low-latency hook: MYRMIDON (or any executor) may POST a wave
    verdict here to advance the DAG immediately instead of waiting for the
    polling reconciler. Idempotent — reconciliation is also done by polling.
    """
    import os
    if x_internal_token != os.getenv("INTERNAL_API_TOKEN", "morpheus-internal-sync-key"):
        raise HTTPException(status_code=403, detail="Invalid internal token")

    mission_control.reconcile_all(db)
    return {"status": "reconciled"}
