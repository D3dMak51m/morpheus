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
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (AdminUser, AgentProfile, Mission, MissionDossier,
                        MissionOutcome, MissionSquad, MissionTarget)
from app.rbac import require_permission
from app import mission_control, mission_recon, mission_validate

logger = logging.getLogger("daedalus.router_missions")

INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "morpheus-internal-sync-key")

router = APIRouter(prefix="/api/v1/missions", tags=["Mission Deck"])

VALID_ROLES = ("alpha", "beta", "gamma")
VALID_TACTICS = ("soft_support", "aggressive_displacement", "dynamic")
VALID_STATUS = ("active", "paused")
VALID_KIND = ("channel", "post")
VALID_TARGET_STATUS = ("active", "suggested", "rejected")


def canonical_identifier(raw: str, kind: str = "channel") -> str:
    """Normalise a target identifier to a form MYRMIDON/Pyrogram can resolve.

    The UI accepts "@username", a "t.me/..." URL, or a numeric chat id — but the
    execution engine can only resolve a clean ``@username`` / numeric ``-100…`` ref.
    A raw URL (``https://t.me/foo``) is silently unresolvable, so the channel is
    dropped and the mission never acts on it. Canonicalising at write time prevents
    that class of dead target. For a ``post`` target the trailing /<id> is kept.
        https://t.me/foo  -> @foo        t.me/foo -> @foo        foo -> @foo
        @foo -> @foo      -100123 -> -100123   https://t.me/c/123/45 -> -100123/45
    """
    s = (raw or "").strip()
    if not s:
        return s
    low = s.lower()
    for pref in ("https://t.me/", "http://t.me/", "t.me/", "https://telegram.me/", "telegram.me/"):
        if low.startswith(pref):
            s = s[len(pref):]
            break
    else:
        if s.startswith("@") or s.lstrip("-").isdigit():
            return s  # already a clean ref
        # bare token (e.g. "foo" or "foo/45") falls through to the path handling below
    s = s.strip("/")
    if s.startswith("c/"):
        # private/linked chat: c/<internal>[/<post>] -> -100<internal>[/<post>]
        parts = s.split("/")
        internal = parts[1] if len(parts) > 1 else ""
        rest = "/".join(parts[2:])
        base = f"-100{internal}" if internal.isdigit() else s
        return f"{base}/{rest}" if (kind == "post" and rest) else base
    parts = s.split("/")
    user = parts[0].lstrip("@")
    if kind == "post" and len(parts) > 1 and parts[1].isdigit():
        return f"@{user}/{parts[1]}"
    return f"@{user}" if user else s


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
    # Stage 38 — the mission as an explicit position (see Mission.our_side).
    our_side: Optional[str] = None
    opponent: Optional[str] = None
    key_points: list[str] = Field(default_factory=list)
    red_lines: list[str] = Field(default_factory=list)
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
    our_side: Optional[str] = None
    opponent: Optional[str] = None
    key_points: Optional[list[str]] = None
    red_lines: Optional[list[str]] = None


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
    # Stage 38 — can the swarm actually comment there? (see MissionTarget.health)
    health: str = "unknown"
    health_reason: Optional[str] = None
    health_checked_at: Any = None

    class Config:
        from_attributes = True


class MissionResponse(BaseModel):
    # Stage 45 — lifecycle phase, alongside the legacy on/off `status`.
    phase: str = "draft"
    recon_at: Any = None
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
    our_side: Optional[str] = None
    opponent: Optional[str] = None
    key_points: list[str] = Field(default_factory=list)
    red_lines: list[str] = Field(default_factory=list)
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
    health = {"ok": 0, "blocked": 0, "unknown": 0, "degraded": 0}
    for t in mission.targets:
        tstat[t.status] = tstat.get(t.status, 0) + 1
        if t.status == "active":
            health[t.health] = health.get(t.health, 0) + 1
    return {
        "status_label": "В процессе" if mission.status == "active" else "На паузе",
        "agents": {**roles, "total": len(mission.squad)},
        "targets": tstat,
        # Blocked targets are the #1 silent killer of a mission — surface the count.
        "target_health": health,
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
        tactic=mission.tactic, status=mission.status,
        phase=mission.phase or "draft", recon_at=mission.recon_at,
        agent_mode=mission.agent_mode,
        dynamic_count=mission.dynamic_count, forced_context=mission.forced_context,
        our_side=mission.our_side, opponent=mission.opponent,
        key_points=list(mission.key_points or []), red_lines=list(mission.red_lines or []),
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
        our_side=request.our_side, opponent=request.opponent,
        key_points=request.key_points, red_lines=request.red_lines,
        status="active",
    )
    for m in request.squad:
        mission.squad.append(MissionSquad(agent_id=m.agent_id, assigned_role=m.assigned_role, status="active"))
    for t in request.targets:
        mission.targets.append(MissionTarget(
            kind=t.kind, identifier=canonical_identifier(t.identifier, t.kind), title=t.title,
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
    for field in ("our_side", "opponent", "key_points", "red_lines"):
        value = getattr(request, field)
        if value is not None:
            setattr(mission, field, value)
    db.commit()
    db.refresh(mission)
    return _serialize(mission, db)


# How many "already said" entries to hand back — enough to stop repetition without
# burying the prompt.
DOSSIER_SAID_LIMIT = int(os.getenv("MISSION_DOSSIER_SAID_LIMIT", "8"))


class DossierIn(BaseModel):
    mission_id: int
    kind: str = Field(..., description="fact | opponent | counter | said")
    content: str = Field(..., min_length=1)
    source_url: Optional[str] = None
    added_by: str = "system"
    related_id: Optional[int] = None
    post_url: Optional[str] = None


@router.post("/internal/dossier")
def dossier_add(
    body: DossierIn,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Add one entry to a mission's case file.

    De-duplicated on (mission, kind, content) — the same argument recorded twice is
    still one argument, and a `said` list full of near-duplicates would push the real
    history out of the prompt.
    """
    if x_internal_token != INTERNAL_API_TOKEN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid internal token.")
    normalised = " ".join((body.content or "").split())[:2000]
    if not normalised:
        raise HTTPException(status_code=400, detail="Empty content.")
    existing = (db.query(MissionDossier)
                .filter(MissionDossier.mission_id == body.mission_id,
                        MissionDossier.kind == body.kind,
                        MissionDossier.content == normalised)
                .first())
    if existing is not None:
        existing.times_used = (existing.times_used or 0) + 1
        db.commit()
        return {"status": "ok", "id": existing.id, "duplicate": True,
                "times_used": existing.times_used}
    row = MissionDossier(
        mission_id=body.mission_id, kind=body.kind, content=normalised,
        source_url=body.source_url, added_by=body.added_by,
        related_id=body.related_id, post_url=body.post_url, times_used=1,
    )
    db.add(row)
    db.commit()
    return {"status": "ok", "id": row.id, "duplicate": False, "times_used": 1}


@router.get("/internal/dossier/{mission_id}")
def dossier_get(
    mission_id: int,
    post_url: Optional[str] = None,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    The case file ORPHEUS reads when writing: established facts, the other side's
    arguments with our counters, and what our people have ALREADY said.

    `said` is scoped to this discussion when `post_url` is given — repeating yourself
    in the same thread is what gives a swarm away, while reusing a good argument in a
    different thread is normal.
    """
    if x_internal_token != INTERNAL_API_TOKEN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid internal token.")
    rows = (db.query(MissionDossier)
            .filter(MissionDossier.mission_id == mission_id)
            .order_by(MissionDossier.created_at.desc())
            .limit(300).all())
    out: dict[str, list] = {"fact": [], "opponent": [], "counter": [], "said": []}
    for r in rows:
        if r.kind == "said" and post_url and r.post_url != post_url:
            continue
        bucket = out.setdefault(r.kind, [])
        if r.kind == "said" and len(bucket) >= DOSSIER_SAID_LIMIT:
            continue
        bucket.append({"id": r.id, "content": r.content, "source_url": r.source_url,
                       "added_by": r.added_by, "related_id": r.related_id,
                       "times_used": r.times_used})
    return {"dossier": out}


@router.get("/{mission_id}/dossier")
def dossier_operator(
    mission_id: int,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("agents:view")),
) -> dict[str, Any]:
    """Operator view of the case file — how the mission built its position, and by whom."""
    rows = (db.query(MissionDossier)
            .filter(MissionDossier.mission_id == mission_id)
            .order_by(MissionDossier.kind.asc(), MissionDossier.created_at.desc())
            .limit(500).all())
    return {"entries": [{
        "id": r.id, "kind": r.kind, "content": r.content, "source_url": r.source_url,
        "added_by": r.added_by, "related_id": r.related_id, "post_url": r.post_url,
        "times_used": r.times_used, "created_at": r.created_at,
    } for r in rows], "total": len(rows)}


MISSION_PHASES = ("draft", "recon", "ready", "active", "paused")


class PhaseRequest(BaseModel):
    phase: str = Field(..., description=" | ".join(MISSION_PHASES))


@router.post("/{mission_id}/phase", response_model=MissionResponse)
def set_phase(
    mission_id: int,
    body: PhaseRequest,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("agents:manage")),
) -> MissionResponse:
    """
    Move a mission through its lifecycle: draft → recon → ready → active → paused.

    Going `active` is refused while the mission has no case file. That is the whole
    point of having a phase instead of a switch: the previous model let a mission start
    arguing before anyone had established what was true, and recon on mission #10 later
    found its own key terms in 0 of 1252 facts — it had been ready to argue about
    transport without one fact about transport.
    """
    phase = (body.phase or "").strip().lower()
    if phase not in MISSION_PHASES:
        raise HTTPException(status_code=400,
                            detail=f"Фаза должна быть из {list(MISSION_PHASES)}.")
    mission = db.query(Mission).filter(Mission.id == mission_id).first()
    if not mission:
        raise HTTPException(status_code=404, detail="Миссия не найдена.")

    if phase == "active":
        facts = (db.query(MissionDossier)
                 .filter(MissionDossier.mission_id == mission_id,
                         MissionDossier.kind == "fact")
                 .count())
        if facts == 0:
            raise HTTPException(
                status_code=409,
                detail="Миссия не может выйти в бой без досье: не установлено ни одного "
                       "факта. Запустите разведку — она скажет, каких источников не хватает.")
        checks = mission_validate.validate_mission({
            "goal": mission.narrative_goal, "stance": mission.stance,
            "our_side": mission.our_side, "opponent": mission.opponent,
            "key_points": mission.key_points or [], "red_lines": mission.red_lines or [],
            "status": "active",
        }, deep=False)
        blocking = [i for i in checks["issues"] if i["severity"] == "error"]
        if blocking:
            raise HTTPException(
                status_code=409,
                detail="Миссия описана противоречиво: "
                       + "; ".join(i["message"][:120] for i in blocking))

    mission.phase = phase
    # `status` stays the switch the engines read, so nothing downstream has to change.
    mission.status = "active" if phase == "active" else "paused"
    db.commit()
    db.refresh(mission)
    return _serialize(mission, db)


@router.post("/{mission_id}/recon")
def mission_recon_run(
    mission_id: int,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("agents:manage")),
) -> dict[str, Any]:
    """
    Build the mission's factual base from the knowledge store, into its dossier.

    A mission used to argue from whatever four facts happened to surface for one
    comment. This gathers them once, for the whole roster, with sources.
    """
    try:
        report = mission_recon.run_recon(db, mission_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Миссия не найдена.")
    mission = db.query(Mission).filter(Mission.id == mission_id).first()
    if mission is not None:
        mission.recon_at = datetime.now(timezone.utc)
        # A recon that found something moves the mission on; one that found nothing
        # leaves it in `recon`, which is the honest state — it still knows nothing.
        if report.get("filed") and mission.phase in ("draft", "recon"):
            mission.phase = "ready"
        elif mission.phase == "draft":
            mission.phase = "recon"
        db.commit()
    report["phase"] = mission.phase if mission else None
    return report


class OutcomeEntryIn(BaseModel):
    mission_id: int
    platform: str = "telegram"
    channel_ref: str = ""
    post_url: str
    mood_before: Optional[str] = None
    thread_size_before: int = 0


@router.post("/internal/outcome-entry")
def outcome_entry(
    body: OutcomeEntryIn,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Open (or top up) the outcome row for one discussion a mission entered.

    Success, as the operator defines it, is a CHANGE of tone plus real people drawn
    into dialogue. This records the "before" reading — the crowd's stance toward us at
    the moment we first spoke there — which until now was computed to pick a tactic and
    immediately discarded. One row per (mission, post): a second comment in the same
    thread increments the counter, it does not reset the baseline.
    """
    if x_internal_token != INTERNAL_API_TOKEN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid internal token.")
    row = (db.query(MissionOutcome)
           .filter(MissionOutcome.mission_id == body.mission_id,
                   MissionOutcome.post_url == body.post_url)
           .first())
    if row is None:
        row = MissionOutcome(
            mission_id=body.mission_id, platform=body.platform,
            channel_ref=body.channel_ref or "", post_url=body.post_url,
            mood_before=body.mood_before, thread_size_before=body.thread_size_before,
            our_comments=1,
        )
        db.add(row)
    else:
        row.our_comments = (row.our_comments or 0) + 1
    db.commit()
    return {"status": "ok", "outcome_id": row.id, "our_comments": row.our_comments}


# How long a discussion is left to develop before we judge whether the tone moved.
OUTCOME_MEASURE_AFTER_HOURS = int(os.getenv("MISSION_OUTCOME_AFTER_HOURS", "6"))


@router.get("/internal/outcomes/pending")
def outcomes_pending(
    limit: int = 20,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Discussions the swarm entered that are due a second reading.

    Measured too early, a thread has not had time to react and every mission would
    look inert; measured never, the mission has no idea whether it achieved anything.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=OUTCOME_MEASURE_AFTER_HOURS)
    rows = (db.query(MissionOutcome, Mission)
            .join(Mission, Mission.id == MissionOutcome.mission_id)
            .filter(MissionOutcome.measured_at.is_(None),
                    MissionOutcome.entered_at <= cutoff)
            .order_by(MissionOutcome.entered_at.asc())
            .limit(min(max(limit, 1), 100))
            .all())
    return {"outcomes": [{
        "id": o.id, "mission_id": o.mission_id, "post_url": o.post_url,
        "channel_ref": o.channel_ref, "mood_before": o.mood_before,
        "thread_size_before": o.thread_size_before,
        "our_side": m.our_side or "", "stance": m.stance or "",
    } for o, m in rows]}


class OutcomeMeasureIn(BaseModel):
    outcome_id: int
    mood_after: Optional[str] = None
    thread_size_after: int = 0
    human_replies: int = 0
    unreadable: bool = False


@router.post("/internal/outcome-measure")
def outcome_measure(
    body: OutcomeMeasureIn,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Record the second reading of a discussion.

    ``unreadable`` closes the row without a verdict (the thread was deleted, the
    channel blocked us). Marking it measured anyway keeps it from being retried
    forever, and a NULL `mood_after` is honest — we do not know, rather than "no
    change".
    """
    if x_internal_token != INTERNAL_API_TOKEN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid internal token.")
    row = db.query(MissionOutcome).filter(MissionOutcome.id == body.outcome_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Outcome not found.")
    if not body.unreadable:
        row.mood_after = body.mood_after
        row.thread_size_after = body.thread_size_after
        row.thread_grew = body.thread_size_after > (row.thread_size_before or 0)
        row.human_replies = max(row.human_replies or 0, body.human_replies)
    row.measured_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "ok", "mood_before": row.mood_before, "mood_after": row.mood_after,
            "thread_grew": row.thread_grew}


@router.get("/{mission_id}/validate")
def validate_mission_endpoint(
    mission_id: int,
    deep: bool = True,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("agents:view")),
) -> dict[str, Any]:
    """
    Sanity-check a mission: does it argue with itself, and is its position usable?

    Advisory only — nothing is blocked. `deep=false` skips the one LLM call and
    returns the instant structural checks.
    """
    mission = db.query(Mission).filter(Mission.id == mission_id).first()
    if not mission:
        raise HTTPException(status_code=404, detail="Миссия не найдена.")
    return mission_validate.validate_mission({
        "goal": mission.narrative_goal, "stance": mission.stance,
        "our_side": mission.our_side, "opponent": mission.opponent,
        "key_points": mission.key_points or [], "red_lines": mission.red_lines or [],
        "status": mission.status,
    }, deep=deep)


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
    ident = canonical_identifier(request.identifier, request.kind)
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

    ident = canonical_identifier(request.identifier, request.kind)
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


# ── Target health (internal: reported by MYRMIDON) ───────────────────────────

VALID_HEALTH = ("unknown", "ok", "blocked", "degraded")


class TargetHealthRequest(BaseModel):
    mission_id: Optional[int] = Field(None, description="ограничить одной миссией")
    identifier: str = Field(..., min_length=1)
    health: str = Field(..., description="ok | blocked | degraded | unknown")
    reason: Optional[str] = None


@router.post("/internal/target-health")
def report_target_health(
    request: TargetHealthRequest,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    MYRMIDON reports whether a target can actually be commented on.

    A channel with comments disabled, no linked discussion group, or one the account
    may not write in is *permanently* unusable — the engine must stop spending its
    scan budget there and the operator must SEE why, instead of a mission that looks
    active but never posts. The same identifier can belong to several missions, so by
    default every mission's copy of it is updated.
    """
    if x_internal_token != INTERNAL_API_TOKEN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid internal token.")
    if request.health not in VALID_HEALTH:
        raise HTTPException(status_code=400, detail=f"Invalid health. Allowed: {list(VALID_HEALTH)}")

    ident = canonical_identifier(request.identifier, "channel")
    q = db.query(MissionTarget).filter(MissionTarget.identifier.in_([ident, request.identifier]))
    if request.mission_id:
        q = q.filter(MissionTarget.mission_id == request.mission_id)
    rows = q.all()
    for target in rows:
        target.health = request.health
        target.health_reason = (request.reason or "")[:1000] or None
        target.health_checked_at = datetime.now(timezone.utc)
    db.commit()
    if rows:
        logger.info("Target health '%s' → %s (%s) on %d target(s).",
                    ident, request.health, (request.reason or "")[:80], len(rows))
    return {"status": "ok", "updated": len(rows), "identifier": ident}


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
