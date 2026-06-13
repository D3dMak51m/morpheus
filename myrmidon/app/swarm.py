"""
MYRMIDON — Swarm Amplification
================================
When an ALPHA agent seeds a comment on a post (autonomous target-channel hit),
the swarm piles on: a couple of BETA agents reinforce its angle and maybe a GAMMA
adds light engagement — all on the SAME post, on their own real accounts, trickled
in over a few minutes so it looks organic rather than a synchronized bot burst.

It reuses the normal cognitive pipeline: each companion gets a generate=True
execution task with role=beta|gamma and the alpha's text as alpha_context, so
ORPHEUS's assemble_mission_prompt produces a role-aware amplifying comment, and the
existing executor posts it (and registers its own dialogue watch).

Guards: only ALPHA seeds amplify; only once per post; only beta/gamma that have an
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


def _companions(sf):
    """Active beta/gamma agents that have an active Telegram account."""
    s = sf()
    try:
        rows = s.execute(text(
            "SELECT a.agent_id, a.caste FROM agent_profiles a "
            "JOIN souls_accounts s ON s.agent_id = a.agent_id "
            "WHERE a.caste IN ('beta','gamma') AND a.status='active' "
            "AND s.platform='telegram' AND s.status='active'"
        )).fetchall()
    except Exception as exc:
        logger.error("swarm: companion query failed: %s", exc)
        return [], []
    finally:
        s.close()
    betas = [r[0] for r in rows if r[1] == "beta"]
    gammas = [r[0] for r in rows if r[1] == "gamma"]
    return betas, gammas


# Emoji reactions gamma agents drop to promote an ally (cheap, no LLM).
GAMMA_EMOJI = ["👍", "🔥", "❤", "👏", "💯", "🤝"]


def _enqueue_beta(agent_id: str, seed_task: dict, alpha_text: str, delay: int) -> None:
    """A beta runs a CHEAP ('lite') supporting comment — not the full alpha pipeline."""
    t = {
        "task_id": str(uuid.uuid4()),
        "agent_id": agent_id,
        "target_platform": "telegram",
        "action_type": "comment",
        "target_url": seed_task.get("target_url"),
        "text_to_publish": "",
        "generate": True,
        "lite": True,            # cheaper generation (no RAG/memory/thread, short)
        "narrative_goal": seed_task.get("narrative_goal") or "",
        "tactic": seed_task.get("tactic") or "soft_support",
        "role": "beta",
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


def amplify_seed(seed_task: dict, alpha_text: str, alpha_ref: Optional[dict] = None) -> None:
    """
    Fan an alpha seed out per caste: BETA = cheap supporting comment, GAMMA = emoji
    reaction on the alpha's comment (``alpha_ref`` = {disc_chat_id, message_id}).
    """
    if not seed_task.get("swarm_seed"):
        return
    target_url = seed_task.get("target_url")
    seed_agent = seed_task.get("agent_id")
    if not target_url or not seed_agent:
        return
    sf = _get_sf()
    if _caste(sf, seed_agent) != "alpha":
        return  # only alpha seeds amplify (keeps the alpha→beta→gamma hierarchy)

    r = _get_redis()
    if not r.set(f"morpheus:amplified:{target_url}", "1", nx=True, ex=86400):
        return  # amplify a given post at most once across the whole swarm

    betas, gammas = _companions(sf)
    react_msg_id = (alpha_ref or {}).get("message_id")
    n = 0

    if betas:
        k = min(len(betas), random.randint(1, AMPLIFY_MAX_BETAS))
        for aid in random.sample(betas, k):
            _enqueue_beta(aid, seed_task, alpha_text, random.randint(AMPLIFY_DELAY_MIN, AMPLIFY_DELAY_MAX))
            n += 1

    # Gamma reacts only if we know the alpha's comment id (so it can target it).
    if gammas and react_msg_id and random.random() < GAMMA_PROBABILITY:
        k = min(len(gammas), AMPLIFY_MAX_GAMMAS)
        for aid in random.sample(gammas, k):
            _enqueue_gamma(aid, seed_task, react_msg_id, random.randint(AMPLIFY_DELAY_MIN, AMPLIFY_DELAY_MAX))
            n += 1

    if n:
        emit_event(seed_agent, "amplify",
                   f"рой подхватывает: {n} (beta-комменты + gamma-реакции) на тот же пост",
                   status="active", target=target_url)
        logger.info("swarm: alpha %s amplified to %d companions on %s", seed_agent, n, target_url)
