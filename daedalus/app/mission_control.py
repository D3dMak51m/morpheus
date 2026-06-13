"""
DAEDALUS — Mission Control / DAG Orchestration Engine (Stage 17)
================================================================
Turns a strategic Mission into a coordinated, multi-wave swarm campaign
modelled as a Directed Acyclic Graph (DAG):

        ┌─────────┐      success      ┌────────┐
        │  ALPHA  │ ───────────────▶ │  BETA  │  (amplify / defend)
        └─────────┘                   └────────┘
              │                       ┌────────┐
              └─────────────────────▶ │ GAMMA  │  (supporting noise)
                                      └────────┘

Wave semantics:
  1. On launch, ALPHA tasks are pushed to Redis `queue:execution_tasks` FIRST.
     BETA and GAMMA squad rows are persisted in state 'locked'.
  2. MYRMIDON executes ALPHA and reports SUCCESS/FAILED back to DAEDALUS via
     `/api/v1/analytics/internal/activity` (logged in `agent_activity_logs`).
  3. A reconciler (polling loop + internal callback) detects the ALPHA verdict:
       - SUCCESS → captures ALPHA context (link), unlocks BETA+GAMMA, pushes
                   them to Redis with that context for amplification.
       - FAILED  → the mission is aborted; downstream waves never release.
  4. When every enlisted agent reaches a terminal state the mission completes.

The engine is intentionally DAEDALUS-only: it requires no changes to the
MYRMIDON executor, relying on the existing task-queue + activity-log contract.
"""

import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import redis
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Mission, MissionSquad, AgentActivityLog, AgentProfile, SoulAccount

logger = logging.getLogger("daedalus.mission_control")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
EXECUTION_TASKS_QUEUE = "queue:execution_tasks"

# Stage 25 — dynamic squad assignment constraints.
# A single bot may serve several missions concurrently (when its characteristics
# fit), but never more than this many *active* missions at once.
MAX_MISSIONS_PER_BOT = int(os.getenv("MAX_MISSIONS_PER_BOT", "5"))
# Missions in these states count against a bot's load; completed/failed free it.
ACTIVE_MISSION_STATES = ("pending", "running", "amplifying")
VALID_ROLES = ("alpha", "beta", "gamma")

# Per-wave execution delay (seconds) — staggers the swarm to look organic.
ALPHA_DELAY_SEC = int(os.getenv("MISSION_ALPHA_DELAY_SEC", "10"))
AMPLIFY_DELAY_SEC = int(os.getenv("MISSION_AMPLIFY_DELAY_SEC", "45"))
RECONCILER_INTERVAL_SEC = int(os.getenv("MISSION_RECONCILER_INTERVAL_SEC", "8"))

_redis_client: Optional[redis.Redis] = None


def _get_redis() -> redis.Redis:
    """Lazily create a shared Redis client (decoded responses)."""
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=0,
            decode_responses=True,
            socket_connect_timeout=5,
        )
    return _redis_client


def infer_platform(target_url: str) -> str:
    """Best-effort platform detection from a target URL."""
    u = (target_url or "").lower()
    if "instagram.com" in u:
        return "instagram"
    if "t.me" in u or "telegram" in u:
        return "telegram"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if "threads.net" in u:
        return "threads"
    if "twitter.com" in u or "x.com" in u:
        return "twitter"
    return "web"


# ── Dynamic squad assignment (Stage 25) ───────────────────────────────────

def agent_active_mission_count(db: Session, agent_id: str, exclude_mission_id: Optional[int] = None) -> int:
    """How many *active* missions a bot currently serves (its load vs the cap)."""
    q = (
        db.query(MissionSquad.mission_id)
        .join(Mission, Mission.id == MissionSquad.mission_id)
        .filter(MissionSquad.agent_id == agent_id, Mission.status.in_(ACTIVE_MISSION_STATES))
    )
    if exclude_mission_id is not None:
        q = q.filter(MissionSquad.mission_id != exclude_mission_id)
    return q.distinct().count()


def _tokenize(*texts: Optional[str]) -> set:
    """Lowercase word set (≥4 chars) used for cheap interest/goal overlap."""
    words: set = set()
    for t in texts:
        if not t:
            continue
        for w in re.findall(r"[\w']+", str(t).lower()):
            if len(w) >= 4:
                words.add(w)
    return words


def score_agent_for_mission(profile: AgentProfile, mission: Mission, role: Optional[str]) -> tuple:
    """
    Rank how well a bot's characteristics fit a mission. Returns (score, reasons).

    Signals: caste↔role affinity, interest/goal keyword overlap, and whether the
    persona's aggression matches the mission tactic. Purely advisory — the hard
    gate (an active account on the mission platform) is enforced by eligibility.
    """
    score = 0.0
    reasons: list[str] = []

    if role and profile.caste == role:
        score += 0.4
        reasons.append(f"caste '{profile.caste}' matches role '{role}'")
    elif role:
        score += 0.1
    else:
        score += 0.2

    mwords = _tokenize(mission.title, mission.narrative_goal, mission.forced_context)
    awords: set = set()
    for interest in (profile.core_interests or []):
        awords |= _tokenize(interest)
    awords |= _tokenize(profile.core_mission, profile.codename, profile.profession)
    overlap = mwords & awords
    if overlap:
        score += min(0.4, 0.1 * len(overlap))
        reasons.append("topic overlap: " + ", ".join(sorted(overlap)[:4]))

    aggression = (profile.communication_style or {}).get("aggression")
    if isinstance(aggression, (int, float)):
        if mission.tactic == "aggressive_displacement" and aggression >= 6:
            score += 0.2
            reasons.append(f"aggression {aggression} fits aggressive tactic")
        elif mission.tactic == "soft_support" and aggression <= 5:
            score += 0.15
            reasons.append(f"measured tone ({aggression}) fits soft support")

    return round(min(score, 1.0), 3), reasons


def eligible_agents_for_mission(
    db: Session,
    mission: Mission,
    role: Optional[str] = None,
    include_enlisted: bool = False,
) -> list[dict]:
    """
    Bots that *can* serve this mission, ranked by match.

    Hard gate: the bot must own an ACTIVE account on the mission's platform — the
    exact precondition whose absence makes a launch fail. Bots already at the
    per-bot mission cap are returned flagged ``at_capacity`` (not silently hidden)
    so the operator understands why they cannot be added.
    """
    platform = mission.platform or infer_platform(mission.target_url)
    rows = (
        db.query(SoulAccount.agent_id)
        .filter(
            SoulAccount.platform == platform,
            SoulAccount.status == "active",
            SoulAccount.agent_id.isnot(None),
        )
        .distinct()
        .all()
    )
    agent_ids = [r[0] for r in rows if r[0]]
    if not agent_ids:
        return []

    profiles = db.query(AgentProfile).filter(AgentProfile.agent_id.in_(agent_ids)).all()
    enlisted = {s.agent_id for s in mission.squad}

    results: list[dict] = []
    for p in profiles:
        if p.status == "suspended":
            continue
        already = p.agent_id in enlisted
        if already and not include_enlisted:
            continue
        load = agent_active_mission_count(db, p.agent_id, exclude_mission_id=mission.id)
        score, reasons = score_agent_for_mission(p, mission, role)
        results.append({
            "agent_id": p.agent_id,
            "codename": p.codename,
            "caste": p.caste,
            "status": p.status,
            "platform": platform,
            "active_mission_load": load,
            "at_capacity": load >= MAX_MISSIONS_PER_BOT,
            "already_enlisted": already,
            "match_score": score,
            "match_reasons": reasons,
        })

    # Assignable (under cap) first, then by match score desc.
    results.sort(key=lambda r: (r["at_capacity"], -r["match_score"], -r["active_mission_load"]))
    return results


def auto_assign_squad(db: Session, mission: Mission, counts: dict[str, int]) -> dict:
    """
    Fill a mission's squad with the best-matching available bots.

    ``counts`` is the *desired total* per role (e.g. {"alpha":1,"beta":2,"gamma":2}).
    Only the deficit relative to the current squad is filled. Bots at the
    per-bot cap are skipped. Returns an assignment report.
    """
    assigned: list[dict] = []
    skipped_at_cap = 0
    enlisted = {s.agent_id for s in mission.squad}

    for role in VALID_ROLES:
        want = int(counts.get(role, 0) or 0)
        have = sum(1 for s in mission.squad if s.assigned_role == role)
        need = max(0, want - have)
        if need <= 0:
            continue

        for cand in eligible_agents_for_mission(db, mission, role=role):
            if need <= 0:
                break
            if cand["agent_id"] in enlisted:
                continue
            if cand["at_capacity"]:
                skipped_at_cap += 1
                continue
            db.add(MissionSquad(
                mission_id=mission.id,
                agent_id=cand["agent_id"],
                assigned_role=role,
            ))
            enlisted.add(cand["agent_id"])
            assigned.append({
                "agent_id": cand["agent_id"],
                "codename": cand["codename"],
                "role": role,
                "match_score": cand["match_score"],
                "match_reasons": cand["match_reasons"],
            })
            need -= 1

    if assigned:
        db.commit()

    requested = {r: int(counts.get(r, 0) or 0) for r in VALID_ROLES}
    return {
        "mission_id": mission.id,
        "assigned": assigned,
        "assigned_count": len(assigned),
        "skipped_at_capacity": skipped_at_cap,
        "requested": requested,
    }


# ── Narrative composition per role/tactic ────────────────────────────────

def _compose_payload_text(mission: Mission, role: str) -> str:
    """
    Build the text a given wave should publish. Real, deterministic copy
    derived from the mission's narrative goal, role, and tactic — never a mock.
    ORPHEUS may later override this; here we guarantee a concrete payload.
    """
    goal = (mission.narrative_goal or mission.title or "").strip()
    aggressive = mission.tactic == "aggressive_displacement"
    forced = (mission.forced_context or "").strip()

    if role == "alpha":
        # Stage 21 — when the operator pins a Forced Context, ground the seed
        # narrative in that exact fact instead of relying on retrieval.
        if forced:
            return f"{goal} — {forced}" if goal else forced
        return goal

    # Beta/Gamma reference the Alpha context to coordinate amplification.
    ctx = mission.alpha_context or "the original comment"
    if role == "beta":
        if aggressive:
            return f"Exactly. Anyone claiming otherwise hasn't looked at the facts. {goal}"
        return f"Well said — completely agree. {goal}"
    # gamma → supporting noise / engagement
    if aggressive:
        return "Why is no one talking about this?? Wake up."
    return "Interesting point, thanks for sharing 👍"


def _build_task(mission: Mission, squad: MissionSquad) -> dict:
    """Assemble a MYRMIDON-compatible execution task for a squad member."""
    delay = ALPHA_DELAY_SEC if squad.assigned_role == "alpha" else AMPLIFY_DELAY_SEC
    return {
        "task_id": squad.task_id or str(uuid.uuid4()),
        "agent_id": squad.agent_id,
        "target_platform": mission.platform or infer_platform(mission.target_url),
        "action_type": "comment",
        "target_url": mission.target_url,
        # Deterministic fallback text — used only if ORPHEUS generation fails.
        "text_to_publish": _compose_payload_text(mission, squad.assigned_role),
        "execution_delay_sec": delay,
        # Stage 25 — have MYRMIDON ask ORPHEUS to write a real, context-aware
        # comment (persona + post context + RAG knowledge + memory + mission).
        "generate": True,
        "narrative_goal": mission.narrative_goal or mission.title,
        # Extra DAG context (ignored by the executor, used for traceability).
        "mission_id": mission.id,
        "role": squad.assigned_role,
        "alpha_context": mission.alpha_context,
        # Stage 21 — operator-forced fact carried for any RAG-aware consumer.
        "forced_context": mission.forced_context,
    }


def _push_to_queue(task: dict) -> None:
    """LPUSH a task so MYRMIDON's BRPOP consumes it FIFO."""
    client = _get_redis()
    client.lpush(EXECUTION_TASKS_QUEUE, json.dumps(task))
    logger.info(
        "Mission %s — queued %s task for agent=%s (task_id=%s)",
        task.get("mission_id"), task.get("role"), task.get("agent_id"), task.get("task_id"),
    )


# ── Launch (dispatch the ALPHA wave) ──────────────────────────────────────

def launch_mission(db: Session, mission: Mission) -> dict:
    """
    Construct the DAG and dispatch the ALPHA wave.

    Alpha squad members are pushed to Redis immediately. Beta/Gamma members
    are persisted as 'locked' and held until the reconciler observes the Alpha
    verdict. Raises ValueError if the mission cannot be launched.
    """
    if mission.status not in ("pending", "failed"):
        raise ValueError(f"Mission {mission.id} is already '{mission.status}' and cannot be launched.")

    alphas = [s for s in mission.squad if s.assigned_role == "alpha"]
    downstream = [s for s in mission.squad if s.assigned_role in ("beta", "gamma")]

    if not alphas:
        raise ValueError("Mission requires at least one ALPHA agent to seed the narrative.")

    if not mission.platform:
        mission.platform = infer_platform(mission.target_url)

    mission.status = "running"
    mission.launched_at = datetime.now(timezone.utc)
    mission.alpha_context = None

    # Dispatch ALPHA first.
    for s in alphas:
        s.task_id = str(uuid.uuid4())
        s.status = "dispatched"
        _push_to_queue(_build_task(mission, s))

    # Lock downstream waves until ALPHA reports success.
    for s in downstream:
        s.status = "locked"
        s.task_id = None

    db.commit()
    logger.info(
        "Mission %s LAUNCHED — %d alpha dispatched, %d downstream locked.",
        mission.id, len(alphas), len(downstream),
    )
    return {
        "mission_id": mission.id,
        "status": mission.status,
        "alpha_dispatched": len(alphas),
        "downstream_locked": len(downstream),
    }


# ── Reconciliation (release downstream waves) ─────────────────────────────

def _alpha_verdict(db: Session, mission: Mission) -> Optional[str]:
    """
    Determine the ALPHA wave verdict by inspecting agent_activity_logs.

    Returns 'success' (all alphas have a SUCCESS log since launch),
    'failed' (every alpha reached a terminal state and none succeeded),
    or None (still in flight).
    """
    alphas = [s for s in mission.squad if s.assigned_role == "alpha"]
    if not alphas:
        return "failed"

    launched = mission.launched_at or datetime.min.replace(tzinfo=timezone.utc)
    succeeded = 0
    terminal = 0

    for s in alphas:
        log = (
            db.query(AgentActivityLog)
            .filter(
                and_(
                    AgentActivityLog.agent_id == s.agent_id,
                    AgentActivityLog.target_url == mission.target_url,
                    AgentActivityLog.created_at >= launched,
                )
            )
            .order_by(AgentActivityLog.created_at.desc())
            .first()
        )
        if log is None:
            continue
        status = (log.status or "").upper()
        if status == "SUCCESS":
            succeeded += 1
            terminal += 1
            s.status = "success"
        elif status in ("FAILED", "ERROR"):
            terminal += 1
            s.status = "failed"

    # Any alpha success is enough to seed amplification.
    if succeeded > 0:
        return "success"
    if terminal == len(alphas):
        return "failed"
    return None


def _downstream_complete(db: Session, mission: Mission) -> bool:
    """Reconcile beta/gamma terminal states and report whether all are done."""
    downstream = [s for s in mission.squad if s.assigned_role in ("beta", "gamma")]
    if not downstream:
        return True

    all_terminal = True
    for s in downstream:
        if s.status in ("success", "failed"):
            continue
        if s.status != "dispatched":
            all_terminal = False
            continue
        launched = mission.launched_at or datetime.min.replace(tzinfo=timezone.utc)
        log = (
            db.query(AgentActivityLog)
            .filter(
                and_(
                    AgentActivityLog.agent_id == s.agent_id,
                    AgentActivityLog.target_url == mission.target_url,
                    AgentActivityLog.created_at >= launched,
                )
            )
            .order_by(AgentActivityLog.created_at.desc())
            .first()
        )
        if log is None:
            all_terminal = False
            continue
        status = (log.status or "").upper()
        if status == "SUCCESS":
            s.status = "success"
        elif status in ("FAILED", "ERROR"):
            s.status = "failed"
        else:
            all_terminal = False
    return all_terminal


def _release_downstream(db: Session, mission: Mission) -> int:
    """Unlock and dispatch BETA + GAMMA waves with the captured Alpha context."""
    if not mission.alpha_context:
        # Best-available amplification context (no comment-link API from executor).
        mission.alpha_context = f"the post by our lead agent on {mission.target_url}"

    released = 0
    for s in mission.squad:
        if s.assigned_role in ("beta", "gamma") and s.status == "locked":
            s.task_id = str(uuid.uuid4())
            s.status = "dispatched"
            _push_to_queue(_build_task(mission, s))
            released += 1
    return released


def reconcile_mission(db: Session, mission: Mission) -> None:
    """Advance a single running/amplifying mission through the DAG."""
    if mission.status == "running":
        verdict = _alpha_verdict(db, mission)
        if verdict == "success":
            mission.alpha_context = f"the post by our lead agent on {mission.target_url}"
            released = _release_downstream(db, mission)
            mission.status = "amplifying" if released else "completed"
            db.commit()
            logger.info(
                "Mission %s — ALPHA succeeded; released %d downstream task(s) → %s.",
                mission.id, released, mission.status,
            )
        elif verdict == "failed":
            mission.status = "failed"
            db.commit()
            logger.warning("Mission %s — ALPHA wave failed; mission aborted.", mission.id)
        return

    if mission.status == "amplifying":
        if _downstream_complete(db, mission):
            mission.status = "completed"
        db.commit()


def reconcile_all(db: Session) -> None:
    """Reconcile every active mission. Safe to call repeatedly."""
    active = db.query(Mission).filter(Mission.status.in_(["running", "amplifying"])).all()
    for mission in active:
        try:
            reconcile_mission(db, mission)
        except Exception:
            logger.exception("Failed to reconcile mission %s", mission.id)
            db.rollback()


# ── Background reconciler thread ──────────────────────────────────────────

_reconciler_thread: Optional[threading.Thread] = None
_reconciler_stop = threading.Event()


def _reconciler_loop() -> None:
    logger.info("Mission reconciler loop started (interval=%ds).", RECONCILER_INTERVAL_SEC)
    while not _reconciler_stop.is_set():
        db = SessionLocal()
        try:
            reconcile_all(db)
        except Exception:
            logger.exception("Mission reconciler tick failed.")
        finally:
            db.close()
        _reconciler_stop.wait(RECONCILER_INTERVAL_SEC)
    logger.info("Mission reconciler loop stopped.")


def start_reconciler() -> None:
    """Start the background DAG reconciler thread (idempotent)."""
    global _reconciler_thread
    if _reconciler_thread and _reconciler_thread.is_alive():
        return
    _reconciler_stop.clear()
    _reconciler_thread = threading.Thread(target=_reconciler_loop, name="mission-reconciler", daemon=True)
    _reconciler_thread.start()
    logger.info("Mission reconciler thread launched.")


def stop_reconciler() -> None:
    """Signal the reconciler thread to stop (used on shutdown)."""
    _reconciler_stop.set()
