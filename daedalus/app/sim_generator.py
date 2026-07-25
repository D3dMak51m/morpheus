"""
DAEDALUS — SIMULATION generation bridge (isolated from production)
==================================================================
Turns simulation personas + missions + sim knowledge into real LLM output, with
a hard wall between the polygon and the live swarm:

  * Requests go to **``queue:sim_gen``** — NOT ``queue:mission_gen`` and never
    ``queue:execution_tasks``. Nothing produced here can be picked up by
    MYRMIDON, so no simulation action can reach a real Telegram channel.
  * ORPHEUS serves it with a dedicated handler (``orpheus/app/simulation.py``)
    that writes NO long-term memory, NO ``morpheus:recent_outputs`` history and
    NO production metrics — a simulation run leaves zero trace on real agents.
  * RAG grounding is retrieved from ``sim_knowledge`` only (this module), and the
    retrieved facts + the final prompt are returned to the UI so the operator can
    actually inspect what the model was told.

Rate limits, active-hours and cooldowns deliberately do NOT apply here: the
polygon is meant for mass generation at full speed.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import redis
from sqlalchemy.orm import Session

from app.models_simulation import (
    SimAccount,
    SimChannel,
    SimComment,
    SimEvent,
    SimJob,
    SimKnowledge,
    SimMission,
    SimPersona,
    SimPost,
)

logger = logging.getLogger("daedalus.sim_generator")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

# Simulation-only Redis surface. Everything the polygon touches is namespaced.
SIM_GEN_QUEUE = "queue:sim_gen"
SIM_REPLY_PREFIX = "reply:simgen:"
SIM_GEN_TIMEOUT = int(os.getenv("SIM_GEN_TIMEOUT_SEC", "180"))

_redis_client: Optional[redis.Redis] = None


def get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis(
            host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True,
            socket_connect_timeout=5, socket_timeout=SIM_GEN_TIMEOUT + 30,
        )
    return _redis_client


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Simulation RAG (sim_knowledge only) ────────────────────────────────────

_WORD_RE = re.compile(r"[а-яёa-z0-9]{4,}", re.IGNORECASE)


def _tokens(text: str) -> set[str]:
    """Stem-ish tokens (first 5 chars) so Russian declensions still match."""
    return {t.lower()[:5] for t in _WORD_RE.findall(text or "")}


def retrieve_knowledge(
    db: Session, world_id: int, query: str, limit: int = 4,
    kinds: tuple[str, ...] = ("fact", "news", "history", "channel_profile"),
) -> list[SimKnowledge]:
    """
    Lexical retrieval over the simulation's OWN knowledge base.

    Deliberately deterministic (token overlap + tag hits + weight) rather than
    embedding-based: a polygon run must be reproducible, must not burn the single
    GPU on embeddings, and must be explainable — the operator sees exactly which
    rows were injected. Production ``knowledge_facts`` are never queried; import
    them into the world first if you want them in play.
    """
    rows = (
        db.query(SimKnowledge)
        .filter(SimKnowledge.world_id == world_id, SimKnowledge.kind.in_(kinds))
        .order_by(SimKnowledge.created_at.desc())
        .limit(400)
        .all()
    )
    if not rows:
        return []
    q = _tokens(query)
    if not q:
        return rows[:limit]

    scored: list[tuple[float, int, SimKnowledge]] = []
    for row in rows:
        body = _tokens(f"{row.title or ''} {row.content}")
        overlap = len(q & body)
        tag_hits = sum(1 for t in (row.tags or []) if _tokens(str(t)) & q)
        score = overlap + 2.0 * tag_hits + 0.25 * float(row.weight or 1)
        if score > 0:
            scored.append((score, row.id, row))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return [r for _, _, r in scored[:limit]]


def system_rules(db: Session, world_id: int) -> list[str]:
    """Operator-authored system rules/prompts injected into every generation."""
    rows = (
        db.query(SimKnowledge)
        .filter(SimKnowledge.world_id == world_id, SimKnowledge.kind.in_(("rule", "prompt")))
        .order_by(SimKnowledge.id.asc())
        .limit(20)
        .all()
    )
    return [r.content for r in rows if (r.content or "").strip()]


# ── Request/reply with ORPHEUS over the simulation queue ───────────────────

def request_generation(payload: dict[str, Any], timeout: int = SIM_GEN_TIMEOUT) -> dict[str, Any]:
    """
    Push one generation request onto ``queue:sim_gen`` and block for the reply.

    Returns ``{status: ok|error, text, prompt, reason, tactic, model}``. A missing
    ORPHEUS (or a dead Ollama) surfaces as ``status='error'`` with a reason — the
    caller records that on the artefact instead of inventing placeholder text.
    """
    request_id = str(uuid.uuid4())
    reply_key = SIM_REPLY_PREFIX + request_id
    body = dict(payload)
    body["request_id"] = request_id
    body["reply_key"] = reply_key
    body["simulation"] = True  # ORPHEUS refuses to persist memory for these

    try:
        client = get_redis()
        client.lpush(SIM_GEN_QUEUE, json.dumps(body, ensure_ascii=False))
        client.expire(SIM_GEN_QUEUE, 3600)
    except Exception as exc:
        logger.error("sim-gen: cannot reach Redis: %s", exc)
        return {"status": "error", "text": "", "reason": f"redis_unavailable: {exc}", "prompt": ""}

    try:
        reply = get_redis().brpop([reply_key], timeout=timeout)
    except Exception as exc:
        logger.error("sim-gen %s: reply wait failed: %s", request_id, exc)
        return {"status": "error", "text": "", "reason": f"reply_wait_failed: {exc}", "prompt": ""}

    if reply is None:
        return {"status": "error", "text": "", "prompt": "",
                "reason": f"orpheus_timeout ({timeout}s) — сервис ORPHEUS не ответил"}
    try:
        data = json.loads(reply[1])
    except Exception as exc:
        return {"status": "error", "text": "", "prompt": "", "reason": f"bad_reply: {exc}"}
    return data


def orpheus_available() -> bool:
    """Cheap liveness probe used by the UI banner (Redis reachable at all)."""
    try:
        return bool(get_redis().ping())
    except Exception:
        return False


# ── Payload assembly ───────────────────────────────────────────────────────

def persona_payload(p: SimPersona) -> dict[str, Any]:
    """The persona travels INLINE — ORPHEUS never looks up a production profile."""
    return {
        "agent_key": p.agent_key,
        "codename": p.codename,
        "full_name": p.full_name,
        "caste": p.caste,
        "bio": p.bio,
        "core_mission": p.core_mission,
        "interests": p.interests or [],
        "style": p.style or {},
        "system_prompt": p.system_prompt or "",
        "settings": p.settings or {},
    }


def mission_payload(m: Optional[SimMission]) -> dict[str, Any]:
    if m is None:
        return {}
    return {
        "id": m.id, "title": m.title, "goal": m.goal, "stance": m.stance,
        "worldview": m.worldview, "tactic": m.tactic, "mode": m.mode,
        "settings": m.settings or {},
    }


def channel_payload(c: Optional[SimChannel]) -> dict[str, Any]:
    if c is None:
        return {}
    return {
        "username": c.username, "title": c.title, "description": c.description,
        "geo_label": c.geo_label, "tags": c.tags or [],
    }


def post_payload(post: SimPost) -> dict[str, Any]:
    media = []
    for m in (post.media or []):
        label = m.get("caption") or m.get("name") or m.get("url") or m.get("kind")
        media.append(f"{m.get('kind', 'media')}: {label}")
    return {
        "text": post.text or "",
        "media_context": "; ".join(media),
        "author": post.author_label or (post.channel.title if post.channel else ""),
        "reactions": post.reactions or {},
    }


def thread_payload(db: Session, post_id: int, limit: int = 12,
                   branch_of: Optional[int] = None) -> list[dict[str, str]]:
    """Existing published comments as conversation context (oldest → newest)."""
    q = (
        db.query(SimComment)
        .filter(SimComment.post_id == post_id, SimComment.status == "published")
        .order_by(SimComment.id.asc())
    )
    rows = q.limit(200).all()
    if branch_of is not None:
        # Replying: give the model the branch it is answering, not the whole thread.
        by_id = {c.id: c for c in rows}
        chain: list[SimComment] = []
        node = by_id.get(branch_of)
        while node is not None:
            chain.append(node)
            node = by_id.get(node.parent_id) if node.parent_id else None
        rows = list(reversed(chain)) or rows
    out = []
    for c in rows[-limit:]:
        out.append({"author": comment_author_label(db, c), "text": (c.text or "")[:400]})
    return out


def comment_author_label(db: Session, c: SimComment) -> str:
    if c.author_label:
        return c.author_label
    if c.author_kind == "account" and c.account_id:
        acc = db.get(SimAccount, c.account_id)
        if acc:
            return acc.display_name
    if c.author_kind == "persona" and c.persona_id:
        p = db.get(SimPersona, c.persona_id)
        if p:
            return p.codename
    return "аноним"


def build_request(
    db: Session,
    world_id: int,
    persona: SimPersona,
    post: SimPost,
    *,
    mode: str = "comment",
    mission: Optional[SimMission] = None,
    parent: Optional[SimComment] = None,
    prompt_override: Optional[str] = None,
    tone: Optional[str] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    avoid: Optional[list[str]] = None,
    rag_limit: int = 4,
) -> tuple[dict[str, Any], list[SimKnowledge]]:
    """Assemble one ORPHEUS request + return the RAG rows used (for the trace)."""
    channel = post.channel
    query = " ".join(filter(None, [
        post.text or "",
        (mission.goal if mission else "") or "",
        (mission.stance if mission else "") or "",
        " ".join(persona.interests or []),
    ]))
    facts = retrieve_knowledge(db, world_id, query, limit=rag_limit)
    payload = {
        "mode": mode,
        "persona": persona_payload(persona),
        "mission": mission_payload(mission),
        "channel": channel_payload(channel),
        "post": post_payload(post),
        "thread": thread_payload(db, post.id, branch_of=parent.id if parent else None),
        "knowledge": [{"title": f.title, "content": f.content, "tags": f.tags or []} for f in facts],
        "rules": system_rules(db, world_id),
        "tone": tone or "",
        "avoid": avoid or [],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "prompt_override": prompt_override or "",
    }
    if parent is not None:
        payload["incoming"] = {
            "author": comment_author_label(db, parent),
            "text": parent.text or "",
        }
    return payload, facts


# ── Event helper (shared with the router) ──────────────────────────────────

def log_event(
    db: Session, world_id: int, kind: str, summary: str, *,
    status: str = "done", actor_kind: Optional[str] = None, actor_label: Optional[str] = None,
    actor_id: Optional[int] = None, channel_id: Optional[int] = None, post_id: Optional[int] = None,
    comment_id: Optional[int] = None, mission_id: Optional[int] = None, job_id: Optional[int] = None,
    detail: Optional[dict] = None, commit: bool = False,
) -> SimEvent:
    """Append to the simulation activity journal (the left column's feed)."""
    ev = SimEvent(
        world_id=world_id, kind=kind, status=status, summary=summary,
        actor_kind=actor_kind, actor_label=actor_label, actor_id=actor_id,
        channel_id=channel_id, post_id=post_id, comment_id=comment_id,
        mission_id=mission_id, job_id=job_id, detail=detail or {},
    )
    db.add(ev)
    if commit:
        db.commit()
    return ev


# ── Mass generation (background job runner) ────────────────────────────────

_jobs_lock = threading.Lock()
_cancelled: set[int] = set()


def cancel_job(job_id: int) -> None:
    with _jobs_lock:
        _cancelled.add(job_id)


def _is_cancelled(job_id: int) -> bool:
    with _jobs_lock:
        return job_id in _cancelled


def run_job_async(job_id: int) -> None:
    """Start a batch run in a daemon thread with its own DB session."""
    t = threading.Thread(target=_run_job, args=(job_id,), daemon=True, name=f"sim-job-{job_id}")
    t.start()


def _pick_author(personas: list[SimPersona], accounts: list[SimAccount], idx: int,
                 order: str) -> tuple[Optional[SimPersona], Optional[SimAccount]]:
    """
    Round-robin (default) walks personas and accounts in turn so a batch really
    is 'several agents AND several accounts at once'; 'random' shuffles; 'serial'
    exhausts personas first. Accounts stay MANUAL executors — in a batch they get
    the persona-less voice (crowd/atmosphere), never an AI personality.
    """
    import random
    pool: list[tuple[Optional[SimPersona], Optional[SimAccount]]] = []
    pool += [(p, None) for p in personas]
    pool += [(None, a) for a in accounts]
    if not pool:
        return None, None
    if order == "random":
        return random.choice(pool)
    if order == "serial":
        return pool[min(idx, len(pool) - 1)]
    return pool[idx % len(pool)]


def _run_job(job_id: int) -> None:
    from app.database import SessionLocal

    db: Session = SessionLocal()
    try:
        job = db.get(SimJob, job_id)
        if job is None:
            return
        job.status = "running"
        db.commit()

        params = job.params or {}
        world_id = job.world_id
        mode = job.mode                     # generate | generate_publish | draft
        count = int(params.get("count") or 1)
        pace = float(params.get("pace_sec") or 0)
        order = params.get("order") or "round_robin"
        tone = params.get("tone") or ""
        temperature = params.get("temperature")
        max_tokens = params.get("max_tokens")
        prompt_override = params.get("prompt_override") or ""
        reply_ratio = float(params.get("reply_ratio") or 0)   # 0..1 — share of replies
        persona_ids = params.get("persona_ids") or []
        account_ids = params.get("account_ids") or []
        account_texts = params.get("account_texts") or []     # manual crowd lines

        post = db.get(SimPost, job.post_id) if job.post_id else None
        mission = db.get(SimMission, job.mission_id) if job.mission_id else None
        if post is None:
            job.status = "error"
            job.message = "Пост не найден — нечего комментировать."
            db.commit()
            return

        personas = (
            db.query(SimPersona).filter(SimPersona.id.in_(persona_ids)).all() if persona_ids else []
        )
        accounts = (
            db.query(SimAccount).filter(SimAccount.id.in_(account_ids)).all() if account_ids else []
        )
        if not personas and not accounts:
            job.status = "error"
            job.message = "Не выбран ни один агент и ни один аккаунт."
            db.commit()
            return

        target_status = {"generate": "generated", "generate_publish": "published",
                         "draft": "draft"}.get(mode, "generated")

        produced: list[str] = []
        for i in range(count):
            if _is_cancelled(job_id):
                job.status = "cancelled"
                job.message = f"Остановлено оператором на {i}/{count}."
                db.commit()
                log_event(db, world_id, "generation", f"Массовая генерация остановлена ({i}/{count})",
                          status="done", job_id=job_id, post_id=post.id, commit=True)
                return

            persona, account = _pick_author(personas, accounts, i, order)

            # Reply-vs-top-level: pick an existing comment to answer.
            parent = None
            if reply_ratio > 0 and (i + 1) / max(count, 1) <= reply_ratio + 1e-9:
                parent = (
                    db.query(SimComment)
                    .filter(SimComment.post_id == post.id, SimComment.status == "published")
                    .order_by(SimComment.id.desc()).first()
                )

            if persona is not None:
                payload, facts = build_request(
                    db, world_id, persona, post,
                    mode="reply" if parent is not None else "comment",
                    mission=mission, parent=parent, prompt_override=prompt_override,
                    tone=tone, max_tokens=max_tokens, temperature=temperature,
                    avoid=produced[-5:],
                )
                result = request_generation(payload)
                text = (result.get("text") or "").strip()
                ok = result.get("status") == "ok" and bool(text)
                comment = SimComment(
                    post_id=post.id, parent_id=parent.id if parent else None,
                    author_kind="persona", persona_id=persona.id,
                    text=text or f"[ошибка генерации: {result.get('reason') or 'пусто'}]",
                    status=target_status if ok else "error",
                    origin="ai", mission_id=mission.id if mission else None, job_id=job_id,
                    meta={
                        "prompt": result.get("prompt", ""),
                        "reason": result.get("reason", ""),
                        "tactic": result.get("tactic") or (mission.tactic if mission else ""),
                        "model": result.get("model", ""),
                        "rag": [{"id": f.id, "title": f.title, "content": f.content[:200]} for f in facts],
                        "job_id": job_id,
                    },
                    published_at=_now() if (ok and target_status == "published") else None,
                )
                db.add(comment)
                db.commit()
                if ok:
                    produced.append(text)
                    job.done += 1
                else:
                    job.failed += 1
                log_event(
                    db, world_id, "comment",
                    (f"{persona.codename}: " + (text[:90] if ok else "ошибка генерации")),
                    status=comment.status, actor_kind="persona", actor_label=persona.codename,
                    actor_id=persona.id, post_id=post.id, comment_id=comment.id,
                    channel_id=post.channel_id, mission_id=mission.id if mission else None,
                    job_id=job_id, detail={"reason": result.get("reason", "")},
                )
            else:
                # A manual account in a batch posts operator-supplied crowd lines.
                idx = i % max(len(account_texts), 1)
                text = (account_texts[idx] if account_texts else "").strip()
                if not text:
                    job.failed += 1
                    log_event(db, world_id, "comment",
                              f"{account.display_name}: пустая реплика — пропущено",
                              status="error", actor_kind="account", actor_label=account.display_name,
                              actor_id=account.id, post_id=post.id, job_id=job_id)
                    db.commit()
                    continue
                comment = SimComment(
                    post_id=post.id, parent_id=parent.id if parent else None,
                    author_kind="account", account_id=account.id, text=text,
                    status=target_status, origin="manual", job_id=job_id,
                    mission_id=mission.id if mission else None,
                    meta={"job_id": job_id},
                    published_at=_now() if target_status == "published" else None,
                )
                db.add(comment)
                db.commit()
                job.done += 1
                log_event(
                    db, world_id, "comment", f"{account.display_name}: {text[:90]}",
                    status=comment.status, actor_kind="account", actor_label=account.display_name,
                    actor_id=account.id, post_id=post.id, comment_id=comment.id,
                    channel_id=post.channel_id, job_id=job_id,
                )
            db.commit()
            if pace > 0 and i < count - 1:
                time.sleep(min(pace, 30))

        job.status = "done"
        job.message = f"Готово: {job.done} успешно, {job.failed} с ошибкой."
        log_event(db, world_id, "generation", job.message, status="done", job_id=job_id,
                  post_id=post.id, mission_id=mission.id if mission else None,
                  actor_kind="system", actor_label="массовая генерация")
        db.commit()
    except Exception as exc:  # pragma: no cover — defensive; the thread must never die silently
        logger.exception("sim job %s crashed: %s", job_id, exc)
        try:
            job = db.get(SimJob, job_id)
            if job:
                job.status = "error"
                job.message = str(exc)[:500]
                db.commit()
        except Exception:
            pass
    finally:
        with _jobs_lock:
            _cancelled.discard(job_id)
        db.close()
