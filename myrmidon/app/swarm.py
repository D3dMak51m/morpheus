"""
MYRMIDON — Swarm Amplification
================================
After the mission's OPENER posts the first argument under a post, the rest of the
roster joins the same discussion — on their own real accounts, trickled in over a few
minutes so it reads as a conversation rather than a synchronized burst.

Stage 46 — each companion does its JOB, not a cheaper version of the opener's:
  * ``support`` answers the objection the thread actually raised (full cognitive
    generation, with a technique deliberately different from the opener's);
  * ``closer`` only speaks when the thread turned hostile, and de-escalates;
  * ``scout`` does not amplify — its work belongs before the argument;
  * a ``gamma`` caste still drops an emoji reaction, because that is a cost decision.
A roster that still carries the legacy castes keeps the old behaviour exactly: beta =
cheap "lite" supporting comment, gamma = reaction. That older shape was one bot
speaking and two repeating it more cheaply — an echo, which is what the roles replace.

Guards: only an opening seed amplifies; only once per post; only members with an
active Telegram account; companion tasks carry no swarm_seed so they never cascade.
"""

import json
import logging
import os
import random
import uuid
from typing import Optional

import redis
from sqlalchemy import text

from app.telemetry import emit as emit_event

logger = logging.getLogger("myrmidon.swarm")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
EXECUTION_TASKS_QUEUE = "queue:execution_tasks"

AMPLIFY_MAX_BETAS = int(os.getenv("SWARM_MAX_BETAS", "2"))
AMPLIFY_MAX_GAMMAS = int(os.getenv("SWARM_MAX_GAMMAS", "1"))
# Full-generation answers to the objection. Two people answering the same objection is
# a discussion; more is a pile-on, and each one costs a serial turn on the single GPU.
AMPLIFY_MAX_SUPPORT = int(os.getenv("SWARM_MAX_SUPPORT", "1"))
# A JOB in the discussion (Stage 46), as opposed to the caste, which is a cost tier.
FUNCTIONAL_ROLES = ("scout", "opener", "support", "closer")
# Real hostility, not emphasis — the twin of `_HEAT_MARKERS` in orpheus/app/persona.py
# (the two services share no code). Exclamation marks deliberately do NOT count: an
# emphatic but civil objection deserves an answer, not a de-escalation.
_HEAT_MARKERS = (
    "нищ", "тупо", "идиот", "дебил", "бред", "прошлый век", "позор", "отстой",
    "ненавиж", "дура", "клоун", "лох", "stupid", "idiot", "trash", "garbage",
    "loser", "pathetic",
)
AMPLIFY_DELAY_MIN = int(os.getenv("SWARM_DELAY_MIN_SEC", "60"))
AMPLIFY_DELAY_MAX = int(os.getenv("SWARM_DELAY_MAX_SEC", "240"))
GAMMA_PROBABILITY = float(os.getenv("SWARM_GAMMA_PROB", "0.9"))

_sf = None
_redis: Optional[redis.Redis] = None


def _get_sf():
    global _sf
    if _sf is None:
        from app.main import connect_postgres
        _sf = connect_postgres()
    return _sf


def _get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True,
                             socket_connect_timeout=5, socket_timeout=15)
    return _redis


def _caste(sf, agent_id: str) -> str:
    s = sf()
    try:
        row = s.execute(text("SELECT caste FROM agent_profiles WHERE agent_id=:a"), {"a": agent_id}).fetchone()
        return (row[0] if row else "") or ""
    except Exception:
        return ""
    finally:
        s.close()


def _companions(sf, mission_id=None, exclude: Optional[str] = None) -> list[dict]:
    """
    Roster members that can join this discussion now: ``[{agent_id, caste, role}]``.

    Scoped to the mission's roster when ``mission_id`` is given, else the whole swarm
    (legacy path, castes only). Filtered by an active profile, an active Telegram
    account and the persona's own active hours.
    """
    s = sf()
    try:
        if mission_id:
            rows = s.execute(text(
                "SELECT a.agent_id, a.caste, q.assigned_role FROM mission_squads q "
                "JOIN agent_profiles a ON a.agent_id = q.agent_id "
                "JOIN souls_accounts so ON so.agent_id = a.agent_id "
                "WHERE q.mission_id = :m AND a.status='active' "
                "AND so.platform='telegram' AND so.status='active'"
            ), {"m": mission_id}).fetchall()
        else:
            rows = s.execute(text(
                "SELECT a.agent_id, a.caste, a.caste FROM agent_profiles a "
                "JOIN souls_accounts so ON so.agent_id = a.agent_id "
                "WHERE a.caste IN ('beta','gamma') AND a.status='active' "
                "AND so.platform='telegram' AND so.status='active'"
            )).fetchall()
    except Exception as exc:
        logger.error("swarm: companion query failed: %s", exc)
        return []
    finally:
        s.close()
    from app import schedule
    out = []
    for agent_id, caste, role in rows:
        if exclude and agent_id == exclude:
            continue
        if not schedule.in_active_hours(sf, agent_id):
            continue
        out.append({"agent_id": agent_id, "caste": (caste or "").lower(),
                    "role": (role or caste or "").lower()})
    return out


# Emoji reactions gamma agents drop to promote an ally (cheap, no LLM).
GAMMA_EMOJI = ["👍", "🔥", "❤", "👏", "💯", "🤝"]


def _enqueue_companion(agent_id: str, seed_task: dict, alpha_text: str, delay: int,
                       role: str, lite: bool) -> None:
    """
    Queue one roster member into the discussion the opener just entered.

    ``lite`` is the COST decision and nothing else: a legacy beta runs the cheap
    branch (no RAG, no memory, no thread), while a member with a real job — answering
    the objection, closing a hostile thread — runs the full cognitive path, because
    "answer the specific objection more cheaply" mostly means "repeat the ally".
    """
    t = {
        "task_id": str(uuid.uuid4()),
        "agent_id": agent_id,
        "target_platform": "telegram",
        "action_type": "comment",
        "target_url": seed_task.get("target_url"),
        "text_to_publish": "",
        "generate": True,
        "lite": lite,
        "narrative_goal": seed_task.get("narrative_goal") or "",
        "stance": seed_task.get("stance") or "",
        "mission_id": seed_task.get("mission_id"),
        # A full-path companion re-resolves its own technique against the same
        # objection, avoiding the one the opener already spent (`avoid_tactic`).
        "tactic": "dynamic" if not lite else (seed_task.get("tactic") or "soft_support"),
        "avoid_tactic": "" if lite else (seed_task.get("tactic") or ""),
        "objection": seed_task.get("objection") or "",
        "objection_id": seed_task.get("objection_id"),
        "position": seed_task.get("position") or {},
        "thread_context": seed_task.get("thread_context") or "",
        "channel_profile": seed_task.get("channel_profile"),  # inherit channel grounding
        "role": role,
        "alpha_context": (alpha_text or "")[:300],
        "execution_delay_sec": delay,
        "source": "swarm_amplify",   # no swarm_seed → never cascades
    }
    _get_redis().lpush(EXECUTION_TASKS_QUEUE, json.dumps(t, ensure_ascii=False))


def _enqueue_gamma(agent_id: str, seed_task: dict, react_msg_id: int, delay: int) -> None:
    """A gamma just drops an emoji reaction on the ally's comment — no thinking."""
    t = {
        "task_id": str(uuid.uuid4()),
        "agent_id": agent_id,
        "target_platform": "telegram",
        "action_type": "react",
        "target_url": seed_task.get("target_url"),
        "react_msg_id": react_msg_id,
        "emoji": random.choice(GAMMA_EMOJI),
        "role": "gamma",
        "execution_delay_sec": delay,
        "source": "swarm_amplify",
    }
    _get_redis().lpush(EXECUTION_TASKS_QUEUE, json.dumps(t, ensure_ascii=False))


def thread_is_hostile(seed_task: dict) -> bool:
    """
    Is this discussion actually hostile — the condition the closer exists for?

    Same rule as the tactic heat test in ORPHEUS: real insults, never mere emphasis.
    An emphatic but civil disagreement is a normal argument that deserves an answer,
    not a de-escalation.
    """
    if (seed_task.get("mood") or "").upper() != "OPPOSE":
        return False
    blob = " ".join([str(seed_task.get("thread_context") or ""),
                     str(seed_task.get("objection") or "")]).lower()
    return any(m in blob for m in _HEAT_MARKERS)


def amplify_seed(seed_task: dict, alpha_text: str, alpha_ref: Optional[dict] = None) -> None:
    """
    Bring the rest of the roster into the discussion the opener just entered.

    ``alpha_ref`` = {disc_chat_id, message_id} of the opener's published comment, which
    is what a gamma's emoji reaction needs to target.
    """
    if not seed_task.get("swarm_seed"):
        return
    target_url = seed_task.get("target_url")
    seed_agent = seed_task.get("agent_id")
    if not target_url or not seed_agent:
        return
    sf = _get_sf()
    # Only an OPENING comment is amplified. The role now says so directly; for a task
    # queued before Stage 46 (or by the legacy DAG) fall back to the caste.
    seed_role = (seed_task.get("role") or "").lower()
    if seed_role not in ("opener", "alpha") and _caste(sf, seed_agent) != "alpha":
        return

    r = _get_redis()
    if not r.set(f"morpheus:amplified:{target_url}", "1", nx=True, ex=86400):
        return  # amplify a given post at most once across the whole swarm

    roster = _companions(sf, seed_task.get("mission_id"), exclude=seed_agent)
    react_msg_id = (alpha_ref or {}).get("message_id")
    hostile = thread_is_hostile(seed_task)
    n = 0

    def _delay() -> int:
        return random.randint(AMPLIFY_DELAY_MIN, AMPLIFY_DELAY_MAX)

    # Members with a real job in the discussion.
    supports = [c for c in roster if c["role"] == "support"]
    closers = [c for c in roster if c["role"] == "closer"]
    # Legacy rosters: a beta backs the ally cheaply, exactly as before.
    betas = [c for c in roster if c["role"] not in FUNCTIONAL_ROLES and c["caste"] == "beta"]
    gammas = [c for c in roster if c["caste"] == "gamma" and c["role"] != "support"]

    for c in supports[:AMPLIFY_MAX_SUPPORT]:
        _enqueue_companion(c["agent_id"], seed_task, alpha_text, _delay(),
                           role="support", lite=False)
        n += 1

    # The closer answers a fight, not a discussion — otherwise it stays out and costs
    # nothing, which is what keeps a full-generation companion affordable.
    if hostile:
        for c in closers[:1]:
            _enqueue_companion(c["agent_id"], seed_task, alpha_text, _delay(),
                               role="closer", lite=False)
            n += 1
    elif closers:
        logger.info("swarm: closer held back on %s — the thread is not hostile.", target_url)

    if betas:
        k = min(len(betas), random.randint(1, AMPLIFY_MAX_BETAS))
        for c in random.sample(betas, k):
            _enqueue_companion(c["agent_id"], seed_task, alpha_text, _delay(),
                               role="beta", lite=True)
            n += 1

    # A reaction needs the opener's message id (so it can target it).
    if gammas and react_msg_id and random.random() < GAMMA_PROBABILITY:
        k = min(len(gammas), AMPLIFY_MAX_GAMMAS)
        for c in random.sample(gammas, k):
            _enqueue_gamma(c["agent_id"], seed_task, react_msg_id, _delay())
            n += 1

    if n:
        emit_event(seed_agent, "amplify",
                   f"команда подключается: {n} (ответы на возражение + реакции) под тем же постом",
                   status="active", target=target_url)
        logger.info("swarm: opener %s brought in %d teammate(s) on %s (hostile=%s)",
                    seed_agent, n, target_url, hostile)
