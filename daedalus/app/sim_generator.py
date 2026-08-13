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
    SimMissionDossier,
    SimMissionOutcome,
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
        # A row must actually MATCH the query. `weight` only breaks ties between rows
        # that already earned a place — it must never manufacture one. Because the old
        # test was `score > 0` and weight contributes 0.25 unconditionally, every row
        # in the world passed: a post about the World Cup final was handed the city's
        # transport budget and a channel's "пробки / новые автобусы" themes, and the
        # model duly wrote about электробусики in a football thread.
        if overlap <= 0 and tag_hits <= 0:
            continue
        score = overlap + 2.0 * tag_hits + 0.25 * float(row.weight or 1)
        scored.append((score, row.id, row))
    scored.sort(key=lambda x: (-x[0], -x[1]))

    # Same fact imported twice is still one fact — it used to occupy two of the four
    # prompt slots (ids 1 and 25 carried identical text).
    out: list[SimKnowledge] = []
    seen: set[str] = set()
    for _, _, row in scored:
        key = " ".join((row.content or "").split()).lower()[:200]
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
        if len(out) >= limit:
            break
    return out


def dossier_for(db: Session, mission_id: Optional[int], post_id: Optional[int],
                said_limit: int = 6) -> dict:
    """
    The mission's shared case file, as production hands it to ORPHEUS.

    `said` is scoped to THIS post: the roster must not replay an argument in the same
    thread, which is what makes three accounts read as one bot. Reusing it elsewhere is
    what a person would do anyway.
    """
    if not mission_id:
        return {}
    rows = (db.query(SimMissionDossier)
            .filter(SimMissionDossier.mission_id == mission_id)
            .order_by(SimMissionDossier.created_at.desc())
            .limit(200).all())
    out: dict[str, list] = {"fact": [], "opponent": [], "counter": [], "said": []}
    for r in rows:
        if r.kind == "said":
            if post_id and r.post_id != post_id:
                continue
            if len(out["said"]) >= said_limit:
                continue
        out.setdefault(r.kind, []).append(
            {"id": r.id, "content": r.content, "source_url": r.source_url,
             "added_by": r.added_by, "times_used": r.times_used})
    return out


def record_said(db: Session, mission_id: Optional[int], post_id: Optional[int],
                text: str, added_by: str) -> None:
    """File the argument just used, so the rest of the roster does not replay it."""
    if not mission_id or not (text or "").strip():
        return
    content = " ".join(text.split())[:600]
    existing = (db.query(SimMissionDossier)
                .filter(SimMissionDossier.mission_id == mission_id,
                        SimMissionDossier.kind == "said",
                        SimMissionDossier.content == content)
                .first())
    if existing is not None:
        existing.times_used = (existing.times_used or 0) + 1
        return
    db.add(SimMissionDossier(mission_id=mission_id, kind="said", content=content,
                             added_by=added_by or "system", post_id=post_id, times_used=1))


# ── Outcome measurement (did the tone move, did anyone engage) ────────────

def _mood_of(thread: list[SimComment], db: Session, our_side: str, post_text: str) -> Optional[str]:
    """Ask ORPHEUS the crowd's stance toward our side over these comments."""
    if not thread:
        return None
    rendered = "\n".join(
        f"{comment_author_label(db, c)}: {(c.text or '')[:300]}" for c in thread[-14:])
    res = request_generation({"mode": "mood", "our_side": our_side,
                              "post_text": post_text[:400], "thread_context": rendered},
                             timeout=90)
    return res.get("mood") if res.get("status") == "ok" else None


def measure_outcome(db: Session, mission: SimMission, post: SimPost,
                    label: Optional[str] = None) -> dict[str, Any]:
    """
    Read the discussion the way the operator judges success: did the tone toward us
    move, and did real people answer us.

    The "after" reading is taken over the replies that came AFTER our first comment,
    never over the whole thread. Measured on this very polygon: on a real 21-comment
    thread one comment — and then a coordinated three — left the whole-thread verdict
    at OPPOSE every time, because three replies among twenty-four cannot move an
    average. A whole-thread reading would report "no effect" for any implementation
    ever built, including a perfect one.

    Nobody spoke after us → `mood_after` stays NULL. "We don't know" is honest;
    "unchanged" would be invented.
    """
    rows = (db.query(SimComment)
            .filter(SimComment.post_id == post.id, SimComment.status == "published")
            .order_by(SimComment.id.asc()).all())
    ours_at = next((i for i, c in enumerate(rows) if c.mission_id == mission.id), None)
    if ours_at is None:
        return {"status": "skipped", "reason": "Миссия ещё не выступала под этим постом."}

    before = [c for c in rows[:ours_at] if c.mission_id != mission.id]
    after = [c for c in rows[ours_at + 1:] if c.mission_id != mission.id]
    ours = [c for c in rows if c.mission_id == mission.id]
    our_side = (mission.our_side or mission.stance or mission.goal or "").strip()
    post_text = (post.text or "").strip()

    row = SimMissionOutcome(
        mission_id=mission.id, post_id=post.id, label=label,
        mood_before=_mood_of(before, db, our_side, post_text),
        mood_after=_mood_of(after, db, our_side, post_text),
        thread_size_before=len(before),
        our_comments=len(ours),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "status": "ok", "id": row.id,
        "mood_before": row.mood_before, "mood_after": row.mood_after,
        "thread_size_before": row.thread_size_before,
        "our_comments": row.our_comments,
        # People who spoke after we did — the engagement half of the measure.
        "replies_after": len(after),
    }


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
        # Stage 41 — same shape production sends as `position`, so ORPHEUS builds
        # the identical block for a polygon run and for a live one.
        "position": {
            "our_side": m.our_side or "",
            "opponent": m.opponent or "",
            "key_points": list(m.key_points or []),
            "red_lines": list(m.red_lines or []),
        },
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
                   branch_of: Optional[int] = None,
                   mission_id: Optional[int] = None) -> list[dict]:
    """
    Existing published comments as conversation context (oldest → newest).

    Each line is flagged ``ours`` when this mission wrote it: the writer must see the
    whole discussion (so it does not repeat its own people), while the judge — the
    crowd's stance, and which objection to answer — must not count us as the crowd.
    """
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
        out.append({"author": comment_author_label(db, c), "text": (c.text or "")[:400],
                    "ours": bool(mission_id and c.mission_id == mission_id)})
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
    role: Optional[str] = None,
    objection: str = "",
    avoid_tactic: str = "",
) -> tuple[dict[str, Any], list[SimKnowledge]]:
    """Assemble one ORPHEUS request + return the RAG rows used (for the trace)."""
    channel = post.channel
    # Retrieval query = the SITUATION (post) + what the mission argues. Deliberately
    # NOT the persona's interests, which production's `rag.fetch_fresh_context` also
    # excludes — the polygon only earns its keep if it reproduces the live path.
    # Measured here: persona `Clone-1-a738` carries interests ["пробки","транспорт",
    # "свет"] left over from earlier testing, and including them pulled the city's
    # transport budget into a comment about the World Cup final. Interests shape the
    # voice, they must not decide what the swarm is deemed to know about the topic.
    query = " ".join(filter(None, [
        post.text or "",
        (mission.goal if mission else "") or "",
        (mission.stance if mission else "") or "",
    ]))
    facts = retrieve_knowledge(db, world_id, query, limit=rag_limit)
    dossier = dossier_for(db, mission.id if mission else None, post.id)
    payload = {
        "mode": mode,
        "persona": persona_payload(persona),
        "mission": mission_payload(mission),
        "channel": channel_payload(channel),
        "post": post_payload(post),
        "thread": thread_payload(db, post.id, branch_of=parent.id if parent else None,
                                 mission_id=mission.id if mission else None),
        # Same shape production sends: what the team established, what the other side
        # argues, and what our people already said in THIS thread.
        "dossier": dossier,
        "knowledge": [{"title": f.title, "content": f.content, "tags": f.tags or []} for f in facts],
        "rules": system_rules(db, world_id),
        "tone": tone or "",
        "avoid": avoid or [],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "prompt_override": prompt_override or "",
        # Stage 46 — the JOB this turn does, the objection the team is answering, and
        # the technique already spent on it. Same three the live path carries between
        # the opener and whoever answers after it.
        "role": role or "",
        "objection": objection or "",
        "avoid_tactic": avoid_tactic or "",
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


# The order a team enters a discussion: open, then answer what was raised against it,
# then take the heat out. After that it keeps answering — nobody needs two closers.
_ROLE_BY_TURN = ("opener", "support", "closer", "support")


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
        # What the team established as it went: the objection it is answering and the
        # technique last spent on it (so the next member does not repeat it).
        objection, last_tactic = "", ""
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
                # Stage 46 — a mission run is a TEAM taking turns: someone opens, the
                # next answers the objection that was actually raised (with a different
                # technique), the third cools the thread down. Without this the polygon
                # rehearsed one bot speaking three times.
                role = _ROLE_BY_TURN[i % len(_ROLE_BY_TURN)] if mission else None
                payload, facts = build_request(
                    db, world_id, persona, post,
                    mode="reply" if parent is not None else "comment",
                    mission=mission, parent=parent, prompt_override=prompt_override,
                    tone=tone, max_tokens=max_tokens, temperature=temperature,
                    avoid=produced[-5:],
                    role=role, objection=objection, avoid_tactic=last_tactic,
                )
                result = request_generation(payload)
                text = (result.get("text") or "").strip()
                ok = result.get("status") == "ok" and bool(text)
                # Carry what this turn established to the next one.
                objection = objection or (result.get("objection") or "")
                last_tactic = result.get("tactic") or last_tactic
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
                        "role": result.get("role", ""),
                        "objection": result.get("objection", ""),
                        "model": result.get("model", ""),
                        "rag": [{"id": f.id, "title": f.title, "content": f.content[:200]} for f in facts],
                        "job_id": job_id,
                    },
                    published_at=_now() if (ok and target_status == "published") else None,
                )
                db.add(comment)
                # File the argument into the mission's shared memory BEFORE the next
                # member speaks. Without this a mission run — the one place the roster
                # actually takes turns — had no common memory at all, and members two
                # and three replayed the opener's line almost word for word.
                if ok and mission is not None:
                    record_said(db, mission.id, post.id, text, persona.codename)
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
