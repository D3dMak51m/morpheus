"""
DAEDALUS — SIMULATION router (isolated Telegram-like test polygon)
==================================================================
The whole polygon API lives under ``/api/v1/simulation`` and touches ONLY the
``sim_*`` tables (see ``app.models_simulation``). Production rows are read for
imports and never written, and generation goes through ``app.sim_generator``'s
private Redis queue — so nothing here can post to a real channel, enlist a real
account or alter a real mission.

Everything is editable by hand: every entity exposes a full PUT, posts keep a
revision history, comments can change author/text/reactions/time/status, and the
mass-generation runner can produce, draft or publish in bulk with no production
rate limits, cooldowns or active-hours gating.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import mission_validate, sim_generator, sim_landscape
from app.database import get_db
from app.models import AdminUser, AgentProfile, SoulAccount
from app.models_simulation import (
    SIM_JOB_MODES,
    SIM_KNOWLEDGE_KINDS,
    SIM_LANDSCAPE_KINDS,
    SimAccount,
    SimChannel,
    SimComment,
    SimEvent,
    SimJob,
    SimKnowledge,
    SimLandscapeSource,
    SimMission,
    SimMissionAgent,
    SimPersona,
    SimPost,
    SimPostRevision,
    SimWorld,
)
from app.rbac import require_permission
from app.sim_generator import log_event

logger = logging.getLogger("daedalus.simulation")

router = APIRouter(prefix="/api/v1/simulation", tags=["Simulation"])

view_perm = require_permission("simulation:view")
edit_perm = require_permission("simulation:manage")

# Reading real Telegram threads needs an MTProto session, which only MYRMIDON holds.
MYRMIDON_DEVICE_URL = os.getenv("MYRMIDON_DEVICE_URL", "http://myrmidon:8003")
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "morpheus-internal-sync-key")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO timestamp from an import payload; None when absent/unparseable.

    Imported posts and comments keep their ORIGINAL time — a thread whose replies all
    carry the import moment reads as a burst, and the polygon exists to reproduce how
    a real discussion unfolded.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# ── Schemas ────────────────────────────────────────────────────────────────

class WorldIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    settings: dict = Field(default_factory=dict)


class ChannelIn(BaseModel):
    world_id: Optional[int] = None
    username: str = Field(..., min_length=1, max_length=200)
    title: str = Field(..., min_length=1, max_length=300)
    description: Optional[str] = None
    avatar_color: str = "indigo"
    subscribers: int = 0
    geo_label: Optional[str] = None
    tags: list[str] = Field(default_factory=list)


class PostIn(BaseModel):
    channel_id: int
    text: str = ""
    media: list[dict] = Field(default_factory=list)
    reactions: dict = Field(default_factory=dict)
    views: int = 0
    pinned: bool = False
    author_label: Optional[str] = None
    published_at: Optional[datetime] = None


class PostPatch(BaseModel):
    channel_id: Optional[int] = None          # переносит пост в другой канал
    text: Optional[str] = None
    media: Optional[list[dict]] = None
    reactions: Optional[dict] = None
    views: Optional[int] = None
    pinned: Optional[bool] = None
    author_label: Optional[str] = None
    published_at: Optional[datetime] = None
    note: Optional[str] = None                # комментарий к правке (в историю)


class CommentIn(BaseModel):
    post_id: int
    parent_id: Optional[int] = None
    author_kind: str = "account"              # account | persona | external
    account_id: Optional[int] = None
    persona_id: Optional[int] = None
    author_label: Optional[str] = None
    text: str = ""
    status: str = "published"
    origin: str = "manual"
    mission_id: Optional[int] = None
    reactions: dict = Field(default_factory=dict)
    published_at: Optional[datetime] = None


class CommentPatch(BaseModel):
    text: Optional[str] = None
    parent_id: Optional[int] = None
    author_kind: Optional[str] = None
    account_id: Optional[int] = None
    persona_id: Optional[int] = None
    author_label: Optional[str] = None
    status: Optional[str] = None
    reactions: Optional[dict] = None
    mission_id: Optional[int] = None
    published_at: Optional[datetime] = None


class ReactionIn(BaseModel):
    emoji: str = Field(..., min_length=1, max_length=16)
    delta: int = 1


class AccountIn(BaseModel):
    world_id: Optional[int] = None
    handle: str = Field(..., min_length=1, max_length=120)
    display_name: str = Field(..., min_length=1, max_length=200)
    initials: str = ""
    color: str = "blue"
    status: str = "active"
    description: Optional[str] = None


class PersonaIn(BaseModel):
    world_id: Optional[int] = None
    agent_key: str = Field(..., min_length=1, max_length=80)
    codename: str = Field(..., min_length=1, max_length=120)
    full_name: Optional[str] = None
    caste: str = "alpha"
    status: str = "active"
    color: str = "violet"
    bio: Optional[str] = None
    core_mission: Optional[str] = None
    interests: list[str] = Field(default_factory=list)
    style: dict = Field(default_factory=dict)
    system_prompt: Optional[str] = None
    settings: dict = Field(default_factory=dict)


class MissionIn(BaseModel):
    world_id: Optional[int] = None
    title: str = Field(..., min_length=1, max_length=200)
    goal: Optional[str] = None
    stance: Optional[str] = None
    worldview: Optional[str] = None
    # Stage 41 — the same explicit position production carries.
    our_side: Optional[str] = None
    opponent: Optional[str] = None
    key_points: list[str] = Field(default_factory=list)
    red_lines: list[str] = Field(default_factory=list)
    tactic: str = "dynamic"
    mode: str = "comment"
    status: str = "active"
    scope: dict = Field(default_factory=dict)
    settings: dict = Field(default_factory=dict)
    agent_ids: list[int] = Field(default_factory=list)


class MissionAgentIn(BaseModel):
    persona_id: int
    role: str = "alpha"


class KnowledgeIn(BaseModel):
    world_id: Optional[int] = None
    kind: str = "fact"
    title: Optional[str] = None
    content: str = Field(..., min_length=1)
    tags: list[str] = Field(default_factory=list)
    source: Optional[str] = None
    weight: int = 1


class KnowledgeImportIn(BaseModel):
    world_id: Optional[int] = None
    # knowledge | landscape | channel_profiles | souls | missions
    what: str = "knowledge"
    limit: int = 100
    layers: list[str] = Field(default_factory=list)
    query: Optional[str] = None
    agent_ids: list[str] = Field(default_factory=list)
    mission_ids: list[int] = Field(default_factory=list)


class LandscapeIn(BaseModel):
    world_id: Optional[int] = None
    kind: str = "rss"
    url: str = ""
    title: Optional[str] = None
    target_channel_id: Optional[int] = None
    options: dict = Field(default_factory=dict)


class ScrapeIn(BaseModel):
    world_id: Optional[int] = None
    kind: str = "rss"
    url: str
    limit: int = 20
    target_channel_id: Optional[int] = None
    as_knowledge: bool = False
    tags: list[str] = Field(default_factory=list)
    save_source: bool = True


class GenerateIn(BaseModel):
    world_id: Optional[int] = None
    post_id: int
    persona_id: int
    parent_id: Optional[int] = None           # ответ на комментарий
    mission_id: Optional[int] = None
    mode: str = "generate"                    # generate | generate_publish | draft
    tone: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    prompt_override: Optional[str] = None
    rag_limit: int = 4


class BatchIn(BaseModel):
    world_id: Optional[int] = None
    post_id: int
    mission_id: Optional[int] = None
    persona_ids: list[int] = Field(default_factory=list)
    account_ids: list[int] = Field(default_factory=list)
    account_texts: list[str] = Field(default_factory=list)
    count: int = Field(3, ge=1, le=200)
    mode: str = "generate_publish"
    order: str = "round_robin"                # round_robin | random | serial
    pace_sec: float = 0
    reply_ratio: float = 0
    tone: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    prompt_override: Optional[str] = None


# ── World helpers ──────────────────────────────────────────────────────────

DEFAULT_WORLD_NAME = "Полигон"


def get_world(db: Session, world_id: Optional[int] = None) -> SimWorld:
    """Resolve the working world; bootstrap a default one on first use."""
    if world_id:
        world = db.get(SimWorld, world_id)
        if world is None:
            raise HTTPException(status_code=404, detail="Симуляция не найдена.")
        return world
    world = db.query(SimWorld).order_by(SimWorld.id.asc()).first()
    if world is None:
        world = SimWorld(
            name=DEFAULT_WORLD_NAME,
            description="Изолированная среда для тестирования агентов, миссий, RAG и промптов.",
            settings={},
        )
        db.add(world)
        db.commit()
        db.refresh(world)
        log_event(db, world.id, "system", "Создан изолированный полигон.", actor_kind="system",
                  actor_label="система", commit=True)
    return world


def _require_in_world(world_id: int, obj_world_id: int) -> None:
    if world_id != obj_world_id:
        raise HTTPException(status_code=400, detail="Объект принадлежит другой симуляции.")


# ── Serialization ──────────────────────────────────────────────────────────

def world_out(db: Session, w: SimWorld) -> dict[str, Any]:
    channels = db.query(func.count(SimChannel.id)).filter(SimChannel.world_id == w.id).scalar() or 0
    posts = (
        db.query(func.count(SimPost.id))
        .join(SimChannel, SimPost.channel_id == SimChannel.id)
        .filter(SimChannel.world_id == w.id).scalar() or 0
    )
    comments = (
        db.query(func.count(SimComment.id))
        .join(SimPost, SimComment.post_id == SimPost.id)
        .join(SimChannel, SimPost.channel_id == SimChannel.id)
        .filter(SimChannel.world_id == w.id).scalar() or 0
    )
    return {
        "id": w.id, "name": w.name, "description": w.description, "settings": w.settings or {},
        "created_at": w.created_at, "updated_at": w.updated_at,
        "counts": {
            "channels": channels, "posts": posts, "comments": comments,
            "accounts": db.query(func.count(SimAccount.id)).filter(SimAccount.world_id == w.id).scalar() or 0,
            "personas": db.query(func.count(SimPersona.id)).filter(SimPersona.world_id == w.id).scalar() or 0,
            "missions": db.query(func.count(SimMission.id)).filter(SimMission.world_id == w.id).scalar() or 0,
            "knowledge": db.query(func.count(SimKnowledge.id)).filter(SimKnowledge.world_id == w.id).scalar() or 0,
        },
    }


def channel_out(db: Session, c: SimChannel) -> dict[str, Any]:
    posts = db.query(func.count(SimPost.id)).filter(SimPost.channel_id == c.id).scalar() or 0
    last = (
        db.query(func.max(SimPost.published_at)).filter(SimPost.channel_id == c.id).scalar()
    )
    return {
        "id": c.id, "world_id": c.world_id, "username": c.username, "title": c.title,
        "description": c.description, "avatar_color": c.avatar_color,
        "subscribers": c.subscribers, "geo_label": c.geo_label, "tags": c.tags or [],
        "source": c.source, "external_ref": c.external_ref,
        "posts_count": posts, "last_post_at": last,
        "created_at": c.created_at, "updated_at": c.updated_at,
    }


def post_out(db: Session, p: SimPost, with_counts: bool = True) -> dict[str, Any]:
    data = {
        "id": p.id, "channel_id": p.channel_id, "text": p.text, "media": p.media or [],
        "reactions": p.reactions or {}, "views": p.views, "pinned": p.pinned,
        "author_label": p.author_label, "source": p.source, "external_ref": p.external_ref,
        "published_at": p.published_at, "created_at": p.created_at, "updated_at": p.updated_at,
        "channel": {"id": p.channel.id, "username": p.channel.username, "title": p.channel.title,
                    "avatar_color": p.channel.avatar_color} if p.channel else None,
    }
    if with_counts:
        data["comments_count"] = (
            db.query(func.count(SimComment.id)).filter(SimComment.post_id == p.id).scalar() or 0
        )
        data["revisions_count"] = (
            db.query(func.count(SimPostRevision.id)).filter(SimPostRevision.post_id == p.id).scalar() or 0
        )
    return data


def comment_out(db: Session, c: SimComment) -> dict[str, Any]:
    label = c.author_label
    color = "gray"
    if c.author_kind == "account" and c.account_id:
        acc = db.get(SimAccount, c.account_id)
        if acc:
            label = label or acc.display_name
            color = acc.color
    elif c.author_kind == "persona" and c.persona_id:
        p = db.get(SimPersona, c.persona_id)
        if p:
            label = label or p.codename
            color = p.color
    return {
        "id": c.id, "post_id": c.post_id, "parent_id": c.parent_id,
        "author_kind": c.author_kind, "account_id": c.account_id, "persona_id": c.persona_id,
        "author_label": label or "аноним", "author_color": color,
        "text": c.text, "reactions": c.reactions or {}, "status": c.status, "origin": c.origin,
        "mission_id": c.mission_id, "job_id": c.job_id, "meta": c.meta or {},
        "published_at": c.published_at, "created_at": c.created_at, "updated_at": c.updated_at,
    }


def account_out(a: SimAccount) -> dict[str, Any]:
    return {
        "id": a.id, "world_id": a.world_id, "handle": a.handle, "display_name": a.display_name,
        "initials": a.initials or (a.display_name[:2].upper() if a.display_name else "??"),
        "color": a.color, "status": a.status, "description": a.description,
        "created_at": a.created_at, "updated_at": a.updated_at,
    }


def persona_out(p: SimPersona) -> dict[str, Any]:
    return {
        "id": p.id, "world_id": p.world_id, "agent_key": p.agent_key, "codename": p.codename,
        "full_name": p.full_name, "caste": p.caste, "status": p.status, "color": p.color,
        "bio": p.bio, "core_mission": p.core_mission, "interests": p.interests or [],
        "style": p.style or {}, "system_prompt": p.system_prompt, "settings": p.settings or {},
        "source_agent_id": p.source_agent_id,
        "created_at": p.created_at, "updated_at": p.updated_at,
    }


def mission_out(db: Session, m: SimMission) -> dict[str, Any]:
    agents = []
    for a in m.agents:
        p = db.get(SimPersona, a.persona_id)
        agents.append({
            "id": a.id, "persona_id": a.persona_id, "role": a.role,
            "codename": p.codename if p else "—", "caste": p.caste if p else "—",
            "color": p.color if p else "gray",
        })
    produced = (
        db.query(func.count(SimComment.id))
        .filter(SimComment.mission_id == m.id).scalar() or 0
    )
    return {
        "id": m.id, "world_id": m.world_id, "title": m.title, "goal": m.goal,
        "stance": m.stance, "worldview": m.worldview,
        "our_side": m.our_side, "opponent": m.opponent,
        "key_points": m.key_points or [], "red_lines": m.red_lines or [],
        "tactic": m.tactic, "mode": m.mode,
        "status": m.status, "scope": m.scope or {}, "settings": m.settings or {},
        "agents": agents, "comments_produced": produced,
        "created_at": m.created_at, "updated_at": m.updated_at,
    }


def knowledge_out(k: SimKnowledge) -> dict[str, Any]:
    return {
        "id": k.id, "world_id": k.world_id, "kind": k.kind, "title": k.title,
        "content": k.content, "tags": k.tags or [], "source": k.source, "origin": k.origin,
        "weight": k.weight, "created_at": k.created_at, "updated_at": k.updated_at,
    }


def landscape_out(s: SimLandscapeSource) -> dict[str, Any]:
    return {
        "id": s.id, "world_id": s.world_id, "kind": s.kind, "url": s.url, "title": s.title,
        "target_channel_id": s.target_channel_id, "options": s.options or {},
        "last_run_at": s.last_run_at, "last_status": s.last_status, "last_message": s.last_message,
        "items_imported": s.items_imported, "created_at": s.created_at,
    }


def event_out(e: SimEvent) -> dict[str, Any]:
    return {
        "id": e.id, "world_id": e.world_id, "kind": e.kind, "status": e.status,
        "actor_kind": e.actor_kind, "actor_label": e.actor_label, "actor_id": e.actor_id,
        "channel_id": e.channel_id, "post_id": e.post_id, "comment_id": e.comment_id,
        "mission_id": e.mission_id, "job_id": e.job_id, "summary": e.summary,
        "detail": e.detail or {}, "created_at": e.created_at,
    }


def job_out(j: SimJob) -> dict[str, Any]:
    return {
        "id": j.id, "world_id": j.world_id, "kind": j.kind, "mode": j.mode, "status": j.status,
        "params": j.params or {}, "post_id": j.post_id, "mission_id": j.mission_id,
        "total": j.total, "done": j.done, "failed": j.failed, "message": j.message,
        "created_at": j.created_at, "updated_at": j.updated_at,
    }


def _post_world_id(db: Session, post: SimPost) -> int:
    channel = post.channel or db.get(SimChannel, post.channel_id)
    return channel.world_id


# ── Worlds ─────────────────────────────────────────────────────────────────

@router.get("/worlds")
def list_worlds(db: Session = Depends(get_db), _u: AdminUser = Depends(view_perm)) -> dict[str, Any]:
    get_world(db)  # bootstrap on first open
    worlds = db.query(SimWorld).order_by(SimWorld.id.asc()).all()
    return {"worlds": [world_out(db, w) for w in worlds]}


@router.post("/worlds", status_code=201)
def create_world(body: WorldIn, db: Session = Depends(get_db),
                 _u: AdminUser = Depends(edit_perm)) -> dict[str, Any]:
    world = SimWorld(name=body.name, description=body.description, settings=body.settings)
    db.add(world)
    db.commit()
    db.refresh(world)
    log_event(db, world.id, "system", f"Создана симуляция «{world.name}».",
              actor_kind="operator", actor_label="оператор", commit=True)
    return world_out(db, world)


@router.put("/worlds/{world_id}")
def update_world(world_id: int, body: WorldIn, db: Session = Depends(get_db),
                 _u: AdminUser = Depends(edit_perm)) -> dict[str, Any]:
    world = get_world(db, world_id)
    world.name, world.description, world.settings = body.name, body.description, body.settings
    db.commit()
    return world_out(db, world)


def _wipe_world_content(db: Session, world_id: int) -> None:
    """
    Delete every row belonging to a world. Done explicitly (rather than relying on
    the DB's ON DELETE CASCADE) so the behaviour is identical on any backend and
    the ORM cascades reach nested posts/comments/revisions.
    """
    for model in (SimJob, SimEvent, SimLandscapeSource, SimKnowledge, SimMission,
                  SimPersona, SimAccount, SimChannel):
        for row in db.query(model).filter(model.world_id == world_id).all():
            db.delete(row)
        db.flush()


@router.delete("/worlds/{world_id}")
def delete_world(world_id: int, db: Session = Depends(get_db),
                 _u: AdminUser = Depends(edit_perm)) -> dict[str, str]:
    world = get_world(db, world_id)
    _wipe_world_content(db, world.id)
    db.delete(world)
    db.commit()
    return {"status": "success", "message": f"Симуляция {world_id} удалена."}


@router.post("/worlds/{world_id}/reset")
def reset_world(world_id: int, db: Session = Depends(get_db),
                _u: AdminUser = Depends(edit_perm)) -> dict[str, Any]:
    """Wipe the polygon's content but keep the world itself."""
    world = get_world(db, world_id)
    _wipe_world_content(db, world.id)
    db.commit()
    log_event(db, world.id, "system", "Полигон очищен.", actor_kind="operator",
              actor_label="оператор", commit=True)
    return world_out(db, world)


@router.post("/worlds/{world_id}/seed")
def seed_world(world_id: int, db: Session = Depends(get_db),
               _u: AdminUser = Depends(edit_perm)) -> dict[str, Any]:
    """
    Fill an empty polygon with a small, coherent starting environment (two
    channels with posts, a few manual accounts, two AI personas, some facts) so
    the operator has something real to click on immediately.
    """
    world = get_world(db, world_id)
    created: dict[str, int] = {"channels": 0, "posts": 0, "accounts": 0, "personas": 0,
                               "knowledge": 0, "comments": 0}

    news = sim_landscape.ensure_channel(
        db, world.id, "poligon_news", "Полигон · Новости", source="manual",
        description="Тестовый новостной канал полигона.")
    city = sim_landscape.ensure_channel(
        db, world.id, "poligon_city", "Полигон · Город", source="manual",
        description="Городские обсуждения: транспорт, дороги, коммуналка.")
    created["channels"] = 2
    news.geo_label, city.geo_label = "Ташкент", "Ташкент"
    news.subscribers, city.subscribers = 24500, 8100
    news.tags, city.tags = ["новости", "город"], ["город", "транспорт"]

    base = _now()
    samples = [
        (news, "Городские власти объявили о расширении сети электробусов: 40 машин выйдут на "
               "маршруты до конца года.", {"👍": 34, "🔥": 12}, []),
        (news, "Тарифы на воду вырастут с первого числа следующего месяца. В мэрии объясняют это "
               "износом сетей.", {"😱": 21, "👎": 44}, []),
        (city, "Опять пробка на кольце — стоим сорок минут. Когда это закончится?",
         {"😢": 15}, [{"kind": "image", "url": "", "name": "traffic.jpg",
                       "caption": "фото пробки на кольцевой"}]),
    ]
    for channel, text, reactions, media in samples:
        db.add(SimPost(channel_id=channel.id, text=text, reactions=reactions, media=media,
                       views=1200, published_at=base - timedelta(hours=created["posts"] + 1)))
        created["posts"] += 1

    for handle, name, color in (("@alisher_t", "Алишер", "blue"), ("@nigora_k", "Нигора", "pink"),
                                ("@skeptic99", "Скептик", "orange")):
        if not db.query(SimAccount).filter(SimAccount.world_id == world.id,
                                           SimAccount.handle == handle).first():
            db.add(SimAccount(world_id=world.id, handle=handle, display_name=name,
                              initials=name[:2].upper(), color=color,
                              description="Ручной аккаунт для массовки."))
            created["accounts"] += 1

    for key, codename, caste, bio, interests in (
        ("sim_alpha", "Алый", "alpha", "инженер-транспортник из Ташкента, 34 года",
         ["транспорт", "город", "инфраструктура"]),
        ("sim_beta", "Тихий", "beta", "бухгалтер, экономит на всём, любит поспорить в чатах",
         ["тарифы", "экономика", "быт"]),
    ):
        if not db.query(SimPersona).filter(SimPersona.world_id == world.id,
                                           SimPersona.agent_key == key).first():
            db.add(SimPersona(
                world_id=world.id, agent_key=key, codename=codename, caste=caste, bio=bio,
                interests=interests,
                style={"tone_level": 7, "vocab_level": 5, "emoji_frequency": 2,
                       "aggression": 4, "quirks": [], "language": "ru"},
                core_mission="Обсуждать городские темы как обычный житель.",
            ))
            created["personas"] += 1

    for content, tags in (
        ("Городской бюджет на транспорт в этом году вырос на 18% — большая часть уходит на "
         "закупку электробусов.", ["транспорт", "бюджет"]),
        ("Износ водопроводных сетей в городе оценивается в 60%; аварийность растёт третий год "
         "подряд.", ["вода", "коммуналка"]),
    ):
        db.add(SimKnowledge(world_id=world.id, kind="fact", content=content, tags=tags,
                            origin="manual", source="seed://polygon"))
        created["knowledge"] += 1

    db.commit()
    log_event(db, world.id, "system",
              f"Полигон заполнен демо-средой: {created['channels']} канала, {created['posts']} поста.",
              actor_kind="operator", actor_label="оператор", detail=created, commit=True)
    return {"status": "ok", "created": created, "world": world_out(db, world)}


@router.get("/state")
def get_state(world_id: Optional[int] = None, db: Session = Depends(get_db),
              _u: AdminUser = Depends(view_perm)) -> dict[str, Any]:
    """One bootstrap call for the console: world + all its top-level entities."""
    world = get_world(db, world_id)
    channels = db.query(SimChannel).filter(SimChannel.world_id == world.id).order_by(SimChannel.id.asc()).all()
    accounts = db.query(SimAccount).filter(SimAccount.world_id == world.id).order_by(SimAccount.id.asc()).all()
    personas = db.query(SimPersona).filter(SimPersona.world_id == world.id).order_by(SimPersona.id.asc()).all()
    missions = db.query(SimMission).filter(SimMission.world_id == world.id).order_by(SimMission.id.desc()).all()
    worlds = db.query(SimWorld).order_by(SimWorld.id.asc()).all()
    return {
        "world": world_out(db, world),
        "worlds": [{"id": w.id, "name": w.name} for w in worlds],
        "channels": [channel_out(db, c) for c in channels],
        "accounts": [account_out(a) for a in accounts],
        "personas": [persona_out(p) for p in personas],
        "missions": [mission_out(db, m) for m in missions],
        "engine": {"available": sim_generator.orpheus_available()},
    }


# ── Channels ───────────────────────────────────────────────────────────────

@router.get("/channels")
def list_channels(world_id: Optional[int] = None, db: Session = Depends(get_db),
                  _u: AdminUser = Depends(view_perm)) -> dict[str, Any]:
    world = get_world(db, world_id)
    rows = db.query(SimChannel).filter(SimChannel.world_id == world.id).order_by(SimChannel.id.asc()).all()
    return {"channels": [channel_out(db, c) for c in rows]}


@router.post("/channels", status_code=201)
def create_channel(body: ChannelIn, db: Session = Depends(get_db),
                   _u: AdminUser = Depends(edit_perm)) -> dict[str, Any]:
    world = get_world(db, body.world_id)
    username = "@" + body.username.lstrip("@")
    if db.query(SimChannel).filter(SimChannel.world_id == world.id,
                                   SimChannel.username == username).first():
        raise HTTPException(status_code=409, detail=f"Канал {username} уже существует.")
    channel = SimChannel(
        world_id=world.id, username=username, title=body.title, description=body.description,
        avatar_color=body.avatar_color, subscribers=body.subscribers, geo_label=body.geo_label,
        tags=body.tags, source="manual",
    )
    db.add(channel)
    db.commit()
    db.refresh(channel)
    log_event(db, world.id, "channel", f"Создан канал {channel.username} «{channel.title}».",
              status="done", actor_kind="operator", actor_label="оператор",
              channel_id=channel.id, commit=True)
    return channel_out(db, channel)


@router.put("/channels/{channel_id}")
def update_channel(channel_id: int, body: ChannelIn, db: Session = Depends(get_db),
                   _u: AdminUser = Depends(edit_perm)) -> dict[str, Any]:
    channel = db.get(SimChannel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Канал не найден.")
    channel.username = "@" + body.username.lstrip("@")
    channel.title = body.title
    channel.description = body.description
    channel.avatar_color = body.avatar_color
    channel.subscribers = body.subscribers
    channel.geo_label = body.geo_label
    channel.tags = body.tags
    db.commit()
    log_event(db, channel.world_id, "channel", f"Изменён канал {channel.username}.",
              actor_kind="operator", actor_label="оператор", channel_id=channel.id, commit=True)
    return channel_out(db, channel)


@router.delete("/channels/{channel_id}")
def delete_channel(channel_id: int, db: Session = Depends(get_db),
                   _u: AdminUser = Depends(edit_perm)) -> dict[str, str]:
    channel = db.get(SimChannel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Канал не найден.")
    world_id, name = channel.world_id, channel.username
    db.delete(channel)
    db.commit()
    log_event(db, world_id, "channel", f"Удалён канал {name} (вместе с постами).",
              actor_kind="operator", actor_label="оператор", commit=True)
    return {"status": "success", "message": f"Канал {name} удалён."}


# ── Posts ──────────────────────────────────────────────────────────────────

@router.get("/posts")
def list_posts(channel_id: Optional[int] = None, world_id: Optional[int] = None,
               limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db),
               _u: AdminUser = Depends(view_perm)) -> dict[str, Any]:
    world = get_world(db, world_id)
    q = db.query(SimPost).join(SimChannel, SimPost.channel_id == SimChannel.id).filter(
        SimChannel.world_id == world.id)
    if channel_id:
        q = q.filter(SimPost.channel_id == channel_id)
    rows = q.order_by(SimPost.pinned.desc(), SimPost.published_at.desc()).limit(limit).all()
    return {"posts": [post_out(db, p) for p in rows]}


@router.get("/posts/{post_id}")
def get_post(post_id: int, db: Session = Depends(get_db),
             _u: AdminUser = Depends(view_perm)) -> dict[str, Any]:
    """A single post with its full comment tree — the thread ('комментарии') mode."""
    post = db.get(SimPost, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Пост не найден.")
    comments = (
        db.query(SimComment).filter(SimComment.post_id == post_id)
        .order_by(SimComment.id.asc()).all()
    )
    return {
        "post": post_out(db, post),
        "comments": [comment_out(db, c) for c in comments],
    }


@router.post("/posts", status_code=201)
def create_post(body: PostIn, db: Session = Depends(get_db),
                _u: AdminUser = Depends(edit_perm)) -> dict[str, Any]:
    channel = db.get(SimChannel, body.channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Канал не найден.")
    post = SimPost(
        channel_id=channel.id, text=body.text, media=body.media, reactions=body.reactions,
        views=body.views, pinned=body.pinned, author_label=body.author_label,
        published_at=body.published_at or _now(), source="manual",
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    log_event(db, channel.world_id, "post", f"Новый пост в {channel.username}: {(post.text or '')[:80]}",
              status="published", actor_kind="operator", actor_label="оператор",
              channel_id=channel.id, post_id=post.id, commit=True)
    return post_out(db, post)


@router.put("/posts/{post_id}")
def update_post(post_id: int, body: PostPatch, db: Session = Depends(get_db),
                _u: AdminUser = Depends(edit_perm)) -> dict[str, Any]:
    """Edit any field of a post (including moving it to another channel). The
    previous state is snapshotted into the revision history first."""
    post = db.get(SimPost, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Пост не найден.")

    db.add(SimPostRevision(
        post_id=post.id, note=body.note, author="оператор",
        snapshot={
            "channel_id": post.channel_id, "text": post.text, "media": post.media or [],
            "reactions": post.reactions or {}, "views": post.views, "pinned": post.pinned,
            "author_label": post.author_label,
            "published_at": post.published_at.isoformat() if post.published_at else None,
        },
    ))

    if body.channel_id is not None and body.channel_id != post.channel_id:
        target = db.get(SimChannel, body.channel_id)
        if target is None:
            raise HTTPException(status_code=404, detail="Целевой канал не найден.")
        post.channel_id = target.id
    # Only fields the client actually sent are touched — so an explicit ``null``
    # (clearing author_label, say) is honoured while omitted fields keep their value.
    sent = body.model_fields_set
    for field in ("text", "media", "reactions", "views", "pinned", "author_label", "published_at"):
        if field in sent:
            setattr(post, field, getattr(body, field))
    db.commit()
    db.refresh(post)
    log_event(db, _post_world_id(db, post), "post", f"Пост #{post.id} изменён вручную.",
              actor_kind="operator", actor_label="оператор", post_id=post.id,
              channel_id=post.channel_id, detail={"note": body.note or ""}, commit=True)
    return post_out(db, post)


@router.delete("/posts/{post_id}")
def delete_post(post_id: int, db: Session = Depends(get_db),
                _u: AdminUser = Depends(edit_perm)) -> dict[str, str]:
    post = db.get(SimPost, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Пост не найден.")
    world_id = _post_world_id(db, post)
    db.delete(post)
    db.commit()
    log_event(db, world_id, "post", f"Пост #{post_id} удалён.", actor_kind="operator",
              actor_label="оператор", commit=True)
    return {"status": "success", "message": f"Пост {post_id} удалён."}


@router.get("/posts/{post_id}/revisions")
def list_revisions(post_id: int, db: Session = Depends(get_db),
                   _u: AdminUser = Depends(view_perm)) -> dict[str, Any]:
    rows = (
        db.query(SimPostRevision).filter(SimPostRevision.post_id == post_id)
        .order_by(SimPostRevision.id.desc()).all()
    )
    return {"revisions": [
        {"id": r.id, "post_id": r.post_id, "snapshot": r.snapshot, "note": r.note,
         "author": r.author, "created_at": r.created_at} for r in rows]}


@router.post("/posts/{post_id}/revisions/{revision_id}/restore")
def restore_revision(post_id: int, revision_id: int, db: Session = Depends(get_db),
                     _u: AdminUser = Depends(edit_perm)) -> dict[str, Any]:
    post = db.get(SimPost, post_id)
    rev = db.get(SimPostRevision, revision_id)
    if post is None or rev is None or rev.post_id != post.id:
        raise HTTPException(status_code=404, detail="Версия не найдена.")
    snap = rev.snapshot or {}
    db.add(SimPostRevision(
        post_id=post.id, note=f"перед откатом к версии #{rev.id}", author="оператор",
        snapshot={
            "channel_id": post.channel_id, "text": post.text, "media": post.media or [],
            "reactions": post.reactions or {}, "views": post.views, "pinned": post.pinned,
            "author_label": post.author_label,
            "published_at": post.published_at.isoformat() if post.published_at else None,
        },
    ))
    post.text = snap.get("text", post.text)
    post.media = snap.get("media", post.media)
    post.reactions = snap.get("reactions", post.reactions)
    post.views = snap.get("views", post.views)
    post.pinned = snap.get("pinned", post.pinned)
    post.author_label = snap.get("author_label", post.author_label)
    if snap.get("channel_id"):
        post.channel_id = snap["channel_id"]
    if snap.get("published_at"):
        try:
            post.published_at = datetime.fromisoformat(snap["published_at"])
        except ValueError:
            pass
    db.commit()
    db.refresh(post)
    log_event(db, _post_world_id(db, post), "post", f"Пост #{post.id} откатан к версии #{rev.id}.",
              actor_kind="operator", actor_label="оператор", post_id=post.id, commit=True)
    return post_out(db, post)


@router.post("/posts/{post_id}/reactions")
def react_to_post(post_id: int, body: ReactionIn, db: Session = Depends(get_db),
                  _u: AdminUser = Depends(edit_perm)) -> dict[str, Any]:
    post = db.get(SimPost, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Пост не найден.")
    reactions = dict(post.reactions or {})
    reactions[body.emoji] = max(0, int(reactions.get(body.emoji, 0)) + body.delta)
    if reactions[body.emoji] == 0:
        reactions.pop(body.emoji, None)
    post.reactions = reactions
    db.commit()
    log_event(db, _post_world_id(db, post), "reaction", f"Реакция {body.emoji} на пост #{post.id}.",
              actor_kind="operator", actor_label="оператор", post_id=post.id,
              channel_id=post.channel_id, commit=True)
    return post_out(db, post)


# ── Comments ───────────────────────────────────────────────────────────────

@router.post("/comments", status_code=201)
def create_comment(body: CommentIn, db: Session = Depends(get_db),
                   _u: AdminUser = Depends(edit_perm)) -> dict[str, Any]:
    post = db.get(SimPost, body.post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Пост не найден.")
    if body.parent_id is not None:
        parent = db.get(SimComment, body.parent_id)
        if parent is None or parent.post_id != post.id:
            raise HTTPException(status_code=400, detail="Родительский комментарий не найден в этом посте.")
    if body.author_kind == "account" and not body.account_id:
        raise HTTPException(status_code=400, detail="Выберите аккаунт-исполнитель.")
    if body.author_kind == "persona" and not body.persona_id:
        raise HTTPException(status_code=400, detail="Выберите агента.")

    comment = SimComment(
        post_id=post.id, parent_id=body.parent_id, author_kind=body.author_kind,
        account_id=body.account_id, persona_id=body.persona_id, author_label=body.author_label,
        text=body.text, status=body.status, origin=body.origin, reactions=body.reactions,
        mission_id=body.mission_id,
        published_at=body.published_at or (_now() if body.status == "published" else None),
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)
    out = comment_out(db, comment)
    log_event(db, _post_world_id(db, post), "comment",
              f"{out['author_label']}: {(comment.text or '')[:90]}",
              status=comment.status, actor_kind=comment.author_kind, actor_label=out["author_label"],
              actor_id=comment.account_id or comment.persona_id, post_id=post.id,
              comment_id=comment.id, channel_id=post.channel_id, mission_id=comment.mission_id,
              commit=True)
    return out


@router.put("/comments/{comment_id}")
def update_comment(comment_id: int, body: CommentPatch, db: Session = Depends(get_db),
                   _u: AdminUser = Depends(edit_perm)) -> dict[str, Any]:
    """Manual edit of ANY comment field — text, author, status, reactions, time."""
    comment = db.get(SimComment, comment_id)
    if comment is None:
        raise HTTPException(status_code=404, detail="Комментарий не найден.")
    if body.parent_id is not None:
        if body.parent_id == comment.id:
            raise HTTPException(status_code=400, detail="Комментарий не может быть ответом сам себе.")
        if body.parent_id:
            parent = db.get(SimComment, body.parent_id)
            if parent is None or parent.post_id != comment.post_id:
                raise HTTPException(status_code=400, detail="Родитель не найден в этом посте.")
    sent = body.model_fields_set
    for field in ("text", "parent_id", "author_kind", "account_id", "persona_id",
                  "author_label", "status", "reactions", "mission_id", "published_at"):
        if field in sent:
            setattr(comment, field, getattr(body, field))
    if comment.status == "published" and comment.published_at is None:
        comment.published_at = _now()
    db.commit()
    db.refresh(comment)
    post = db.get(SimPost, comment.post_id)
    out = comment_out(db, comment)
    log_event(db, _post_world_id(db, post), "comment", f"Комментарий #{comment.id} изменён вручную.",
              status=comment.status, actor_kind="operator", actor_label="оператор",
              post_id=comment.post_id, comment_id=comment.id, commit=True)
    return out


@router.delete("/comments/{comment_id}")
def delete_comment(comment_id: int, db: Session = Depends(get_db),
                   _u: AdminUser = Depends(edit_perm)) -> dict[str, str]:
    comment = db.get(SimComment, comment_id)
    if comment is None:
        raise HTTPException(status_code=404, detail="Комментарий не найден.")
    post = db.get(SimPost, comment.post_id)
    world_id = _post_world_id(db, post)
    db.delete(comment)
    db.commit()
    log_event(db, world_id, "comment", f"Комментарий #{comment_id} удалён (с ветвью ответов).",
              actor_kind="operator", actor_label="оператор", post_id=post.id, commit=True)
    return {"status": "success", "message": f"Комментарий {comment_id} удалён."}


@router.post("/comments/{comment_id}/reactions")
def react_to_comment(comment_id: int, body: ReactionIn, db: Session = Depends(get_db),
                     _u: AdminUser = Depends(edit_perm)) -> dict[str, Any]:
    comment = db.get(SimComment, comment_id)
    if comment is None:
        raise HTTPException(status_code=404, detail="Комментарий не найден.")
    reactions = dict(comment.reactions or {})
    reactions[body.emoji] = max(0, int(reactions.get(body.emoji, 0)) + body.delta)
    if reactions[body.emoji] == 0:
        reactions.pop(body.emoji, None)
    comment.reactions = reactions
    db.commit()
    db.refresh(comment)
    return comment_out(db, comment)


@router.post("/comments/{comment_id}/publish")
def publish_comment(comment_id: int, db: Session = Depends(get_db),
                    _u: AdminUser = Depends(edit_perm)) -> dict[str, Any]:
    """Promote a draft/generated comment to published (the polygon's 'send')."""
    comment = db.get(SimComment, comment_id)
    if comment is None:
        raise HTTPException(status_code=404, detail="Комментарий не найден.")
    comment.status = "published"
    comment.published_at = comment.published_at or _now()
    db.commit()
    db.refresh(comment)
    post = db.get(SimPost, comment.post_id)
    out = comment_out(db, comment)
    log_event(db, _post_world_id(db, post), "comment",
              f"Опубликован комментарий {out['author_label']}: {(comment.text or '')[:80]}",
              status="published", actor_kind=comment.author_kind, actor_label=out["author_label"],
              post_id=post.id, comment_id=comment.id, channel_id=post.channel_id, commit=True)
    return out


# ── Accounts (manual executors) ────────────────────────────────────────────

@router.get("/accounts")
def list_accounts(world_id: Optional[int] = None, db: Session = Depends(get_db),
                  _u: AdminUser = Depends(view_perm)) -> dict[str, Any]:
    world = get_world(db, world_id)
    rows = db.query(SimAccount).filter(SimAccount.world_id == world.id).order_by(SimAccount.id.asc()).all()
    return {"accounts": [account_out(a) for a in rows]}


@router.post("/accounts", status_code=201)
def create_account(body: AccountIn, db: Session = Depends(get_db),
                   _u: AdminUser = Depends(edit_perm)) -> dict[str, Any]:
    world = get_world(db, body.world_id)
    handle = "@" + body.handle.lstrip("@")
    if db.query(SimAccount).filter(SimAccount.world_id == world.id,
                                   SimAccount.handle == handle).first():
        raise HTTPException(status_code=409, detail=f"Аккаунт {handle} уже существует.")
    account = SimAccount(
        world_id=world.id, handle=handle, display_name=body.display_name,
        initials=body.initials or body.display_name[:2].upper(), color=body.color,
        status=body.status, description=body.description,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    log_event(db, world.id, "account", f"Создан ручной аккаунт {handle} ({account.display_name}).",
              actor_kind="operator", actor_label="оператор", actor_id=account.id, commit=True)
    return account_out(account)


@router.put("/accounts/{account_id}")
def update_account(account_id: int, body: AccountIn, db: Session = Depends(get_db),
                   _u: AdminUser = Depends(edit_perm)) -> dict[str, Any]:
    account = db.get(SimAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Аккаунт не найден.")
    account.handle = "@" + body.handle.lstrip("@")
    account.display_name = body.display_name
    account.initials = body.initials or body.display_name[:2].upper()
    account.color = body.color
    account.status = body.status
    account.description = body.description
    db.commit()
    return account_out(account)


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int, db: Session = Depends(get_db),
                   _u: AdminUser = Depends(edit_perm)) -> dict[str, str]:
    account = db.get(SimAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Аккаунт не найден.")
    world_id, handle = account.world_id, account.handle
    db.delete(account)
    db.commit()
    log_event(db, world_id, "account", f"Удалён аккаунт {handle}.", actor_kind="operator",
              actor_label="оператор", commit=True)
    return {"status": "success", "message": f"Аккаунт {handle} удалён."}


# ── Personas (AI agents under test) ────────────────────────────────────────

@router.get("/personas")
def list_personas(world_id: Optional[int] = None, db: Session = Depends(get_db),
                  _u: AdminUser = Depends(view_perm)) -> dict[str, Any]:
    world = get_world(db, world_id)
    rows = db.query(SimPersona).filter(SimPersona.world_id == world.id).order_by(SimPersona.id.asc()).all()
    return {"personas": [persona_out(p) for p in rows]}


@router.post("/personas", status_code=201)
def create_persona(body: PersonaIn, db: Session = Depends(get_db),
                   _u: AdminUser = Depends(edit_perm)) -> dict[str, Any]:
    world = get_world(db, body.world_id)
    if db.query(SimPersona).filter(SimPersona.world_id == world.id,
                                   SimPersona.agent_key == body.agent_key).first():
        raise HTTPException(status_code=409, detail=f"Агент '{body.agent_key}' уже существует.")
    persona = SimPersona(
        world_id=world.id, agent_key=body.agent_key, codename=body.codename,
        full_name=body.full_name, caste=body.caste, status=body.status, color=body.color,
        bio=body.bio, core_mission=body.core_mission, interests=body.interests,
        style=body.style, system_prompt=body.system_prompt, settings=body.settings,
    )
    db.add(persona)
    db.commit()
    db.refresh(persona)
    log_event(db, world.id, "agent", f"Создан агент «{persona.codename}» ({persona.caste}).",
              actor_kind="operator", actor_label="оператор", actor_id=persona.id, commit=True)
    return persona_out(persona)


@router.put("/personas/{persona_id}")
def update_persona(persona_id: int, body: PersonaIn, db: Session = Depends(get_db),
                   _u: AdminUser = Depends(edit_perm)) -> dict[str, Any]:
    """Full manual edit. The UI autosaves here, so tuned patterns are never lost."""
    persona = db.get(SimPersona, persona_id)
    if persona is None:
        raise HTTPException(status_code=404, detail="Агент не найден.")
    persona.agent_key = body.agent_key
    persona.codename = body.codename
    persona.full_name = body.full_name
    persona.caste = body.caste
    persona.status = body.status
    persona.color = body.color
    persona.bio = body.bio
    persona.core_mission = body.core_mission
    persona.interests = body.interests
    persona.style = body.style
    persona.system_prompt = body.system_prompt
    persona.settings = body.settings
    db.commit()
    db.refresh(persona)
    return persona_out(persona)


@router.delete("/personas/{persona_id}")
def delete_persona(persona_id: int, db: Session = Depends(get_db),
                   _u: AdminUser = Depends(edit_perm)) -> dict[str, str]:
    persona = db.get(SimPersona, persona_id)
    if persona is None:
        raise HTTPException(status_code=404, detail="Агент не найден.")
    world_id, name = persona.world_id, persona.codename
    db.delete(persona)
    db.commit()
    log_event(db, world_id, "agent", f"Удалён агент «{name}».", actor_kind="operator",
              actor_label="оператор", commit=True)
    return {"status": "success", "message": f"Агент {name} удалён."}


@router.get("/personas/{persona_id}/export")
def export_persona(persona_id: int, db: Session = Depends(get_db),
                   _u: AdminUser = Depends(view_perm)) -> dict[str, Any]:
    """
    A tested simulation persona shaped as a production soul draft, so the
    operator can pick it while creating/editing a REAL agent. This returns data
    only — it never writes to ``agent_profiles``; the operator saves the real
    soul through the normal Souls screen.
    """
    persona = db.get(SimPersona, persona_id)
    if persona is None:
        raise HTTPException(status_code=404, detail="Агент не найден.")
    style = persona.style or {}
    return {
        "agent_id": persona.agent_key,
        "codename": persona.codename,
        "caste": persona.caste,
        "full_name": persona.full_name or persona.codename,
        "core_mission": persona.core_mission,
        "core_interests": persona.interests or [],
        "communication_style": {
            "tone_level": int(style.get("tone_level", 5)),
            "vocab_level": int(style.get("vocab_level", 5)),
            "emoji_frequency": int(style.get("emoji_frequency", 3)),
            "aggression": int(style.get("aggression", 3)),
            "quirks": list(style.get("quirks") or []),
        },
        "behavioral_rules": (persona.settings or {}).get("behavioral_rules") or {},
        "bio": persona.bio,
        "system_prompt": persona.system_prompt,
        "source": "simulation",
    }


# ── Missions (simulation-only) ─────────────────────────────────────────────

@router.get("/missions")
def list_missions(world_id: Optional[int] = None, db: Session = Depends(get_db),
                  _u: AdminUser = Depends(view_perm)) -> dict[str, Any]:
    world = get_world(db, world_id)
    rows = db.query(SimMission).filter(SimMission.world_id == world.id).order_by(SimMission.id.desc()).all()
    return {"missions": [mission_out(db, m) for m in rows]}


@router.post("/missions", status_code=201)
def create_mission(body: MissionIn, db: Session = Depends(get_db),
                   _u: AdminUser = Depends(edit_perm)) -> dict[str, Any]:
    world = get_world(db, body.world_id)
    mission = SimMission(
        world_id=world.id, title=body.title, goal=body.goal, stance=body.stance,
        worldview=body.worldview, our_side=body.our_side, opponent=body.opponent,
        key_points=body.key_points, red_lines=body.red_lines,
        tactic=body.tactic, mode=body.mode, status=body.status,
        scope=body.scope, settings=body.settings,
    )
    db.add(mission)
    db.flush()
    for pid in body.agent_ids:
        persona = db.get(SimPersona, pid)
        if persona and persona.world_id == world.id:
            db.add(SimMissionAgent(mission_id=mission.id, persona_id=pid, role=persona.caste))
    db.commit()
    db.refresh(mission)
    log_event(db, world.id, "mission", f"Создана миссия «{mission.title}» ({len(body.agent_ids)} агентов).",
              actor_kind="operator", actor_label="оператор", mission_id=mission.id, commit=True)
    return mission_out(db, mission)


@router.put("/missions/{mission_id}")
def update_mission(mission_id: int, body: MissionIn, db: Session = Depends(get_db),
                   _u: AdminUser = Depends(edit_perm)) -> dict[str, Any]:
    mission = db.get(SimMission, mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Миссия не найдена.")
    mission.title = body.title
    mission.goal = body.goal
    mission.stance = body.stance
    mission.worldview = body.worldview
    mission.our_side = body.our_side
    mission.opponent = body.opponent
    mission.key_points = body.key_points
    mission.red_lines = body.red_lines
    mission.tactic = body.tactic
    mission.mode = body.mode
    mission.status = body.status
    mission.scope = body.scope
    mission.settings = body.settings
    if body.agent_ids:
        current = {a.persona_id for a in mission.agents}
        wanted = set(body.agent_ids)
        for a in list(mission.agents):
            if a.persona_id not in wanted:
                db.delete(a)
        for pid in wanted - current:
            persona = db.get(SimPersona, pid)
            if persona and persona.world_id == mission.world_id:
                db.add(SimMissionAgent(mission_id=mission.id, persona_id=pid, role=persona.caste))
    db.commit()
    db.refresh(mission)
    return mission_out(db, mission)


@router.delete("/missions/{mission_id}")
def delete_mission(mission_id: int, db: Session = Depends(get_db),
                   _u: AdminUser = Depends(edit_perm)) -> dict[str, str]:
    mission = db.get(SimMission, mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Миссия не найдена.")
    world_id, title = mission.world_id, mission.title
    db.delete(mission)
    db.commit()
    log_event(db, world_id, "mission", f"Удалена миссия «{title}».", actor_kind="operator",
              actor_label="оператор", commit=True)
    return {"status": "success", "message": f"Миссия «{title}» удалена."}


@router.post("/missions/{mission_id}/agents", status_code=201)
def add_mission_agent(mission_id: int, body: MissionAgentIn, db: Session = Depends(get_db),
                      _u: AdminUser = Depends(edit_perm)) -> dict[str, Any]:
    mission = db.get(SimMission, mission_id)
    persona = db.get(SimPersona, body.persona_id)
    if mission is None or persona is None:
        raise HTTPException(status_code=404, detail="Миссия или агент не найдены.")
    _require_in_world(mission.world_id, persona.world_id)
    if db.query(SimMissionAgent).filter(SimMissionAgent.mission_id == mission_id,
                                        SimMissionAgent.persona_id == body.persona_id).first():
        raise HTTPException(status_code=409, detail="Агент уже в миссии.")
    db.add(SimMissionAgent(mission_id=mission_id, persona_id=body.persona_id, role=body.role))
    db.commit()
    db.refresh(mission)
    return mission_out(db, mission)


@router.delete("/missions/{mission_id}/agents/{persona_id}")
def remove_mission_agent(mission_id: int, persona_id: int, db: Session = Depends(get_db),
                         _u: AdminUser = Depends(edit_perm)) -> dict[str, Any]:
    mission = db.get(SimMission, mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Миссия не найдена.")
    row = db.query(SimMissionAgent).filter(SimMissionAgent.mission_id == mission_id,
                                           SimMissionAgent.persona_id == persona_id).first()
    if row:
        db.delete(row)
        db.commit()
        db.refresh(mission)
    return mission_out(db, mission)


class MissionRunIn(BaseModel):
    post_id: Optional[int] = None
    mode: str = "generate_publish"
    per_agent: int = Field(1, ge=1, le=20)
    pace_sec: float = 0
    tone: Optional[str] = None


@router.post("/missions/{mission_id}/run")
def run_mission(mission_id: int, body: MissionRunIn, db: Session = Depends(get_db),
                _u: AdminUser = Depends(edit_perm)) -> dict[str, Any]:
    """
    Launch the mission's whole roster against a post INSIDE the polygon. This is
    the only way a simulation mission ever executes — there is no target engine,
    no Telegram driver and no execution queue behind it.
    """
    mission = db.get(SimMission, mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Миссия не найдена.")
    if mission.status != "active":
        raise HTTPException(status_code=400, detail="Миссия на паузе — запустите её сначала.")
    persona_ids = [a.persona_id for a in mission.agents]
    if not persona_ids:
        raise HTTPException(status_code=400, detail="В миссии нет агентов.")

    post_id = body.post_id or (mission.scope or {}).get("post_id")
    if not post_id:
        # Fall back to the newest post in the mission's scoped channels.
        channel_ids = (mission.scope or {}).get("channel_ids") or []
        q = db.query(SimPost).join(SimChannel, SimPost.channel_id == SimChannel.id).filter(
            SimChannel.world_id == mission.world_id)
        if channel_ids:
            q = q.filter(SimPost.channel_id.in_(channel_ids))
        newest = q.order_by(SimPost.published_at.desc()).first()
        if newest is None:
            raise HTTPException(status_code=400, detail="Не найден пост для работы миссии.")
        post_id = newest.id

    post = db.get(SimPost, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Пост не найден.")
    _require_in_world(mission.world_id, _post_world_id(db, post))

    job = SimJob(
        world_id=mission.world_id, kind="comments", mode=body.mode, status="queued",
        post_id=post.id, mission_id=mission.id,
        params={"count": len(persona_ids) * body.per_agent, "persona_ids": persona_ids,
                "order": "round_robin", "pace_sec": body.pace_sec, "tone": body.tone or ""},
        total=len(persona_ids) * body.per_agent,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    log_event(db, mission.world_id, "mission",
              f"Запуск миссии «{mission.title}»: {job.total} реплик от {len(persona_ids)} агентов.",
              status="scheduled", actor_kind="mission", actor_label=mission.title,
              mission_id=mission.id, post_id=post.id, job_id=job.id, commit=True)
    sim_generator.run_job_async(job.id)
    return job_out(job)


# ── Knowledge ──────────────────────────────────────────────────────────────

@router.get("/knowledge")
def list_knowledge(world_id: Optional[int] = None, kind: Optional[str] = None,
                   limit: int = Query(300, ge=1, le=1000), db: Session = Depends(get_db),
                   _u: AdminUser = Depends(view_perm)) -> dict[str, Any]:
    world = get_world(db, world_id)
    q = db.query(SimKnowledge).filter(SimKnowledge.world_id == world.id)
    if kind:
        q = q.filter(SimKnowledge.kind == kind)
    rows = q.order_by(SimKnowledge.id.desc()).limit(limit).all()
    return {"knowledge": [knowledge_out(k) for k in rows], "total": len(rows)}


@router.post("/knowledge", status_code=201)
def create_knowledge(body: KnowledgeIn, db: Session = Depends(get_db),
                     _u: AdminUser = Depends(edit_perm)) -> dict[str, Any]:
    world = get_world(db, body.world_id)
    if body.kind not in SIM_KNOWLEDGE_KINDS:
        raise HTTPException(status_code=400, detail=f"Тип знания должен быть из {list(SIM_KNOWLEDGE_KINDS)}.")
    row = SimKnowledge(
        world_id=world.id, kind=body.kind, title=body.title, content=body.content,
        tags=body.tags, source=body.source, origin="manual", weight=body.weight,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    log_event(db, world.id, "knowledge", f"Добавлено знание ({row.kind}): {row.content[:80]}",
              actor_kind="operator", actor_label="оператор", commit=True)
    return knowledge_out(row)


@router.put("/knowledge/{knowledge_id}")
def update_knowledge(knowledge_id: int, body: KnowledgeIn, db: Session = Depends(get_db),
                     _u: AdminUser = Depends(edit_perm)) -> dict[str, Any]:
    row = db.get(SimKnowledge, knowledge_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Знание не найдено.")
    row.kind, row.title, row.content = body.kind, body.title, body.content
    row.tags, row.source, row.weight = body.tags, body.source, body.weight
    db.commit()
    db.refresh(row)
    return knowledge_out(row)


@router.delete("/knowledge/{knowledge_id}")
def delete_knowledge(knowledge_id: int, db: Session = Depends(get_db),
                     _u: AdminUser = Depends(edit_perm)) -> dict[str, str]:
    row = db.get(SimKnowledge, knowledge_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Знание не найдено.")
    db.delete(row)
    db.commit()
    return {"status": "success", "message": f"Знание {knowledge_id} удалено."}


@router.post("/knowledge/import")
def import_knowledge(body: KnowledgeImportIn, db: Session = Depends(get_db),
                     _u: AdminUser = Depends(edit_perm)) -> dict[str, Any]:
    """
    Copy production material into this world. READ-ONLY on the production side:
    every importer issues SELECTs and writes only ``sim_*`` rows.
    """
    world = get_world(db, body.world_id)
    report: dict[str, Any] = {"what": body.what}

    if body.what == "knowledge":
        report["imported"] = sim_landscape.import_knowledge_facts(
            db, world.id, limit=body.limit, layers=body.layers or None, query=body.query)
        summary = f"Импортировано фактов из основной базы знаний: {report['imported']}."
    elif body.what == "landscape":
        report["imported"] = sim_landscape.import_landscape_sources(db, world.id)
        summary = f"Импортировано источников ландшафта: {report['imported']}."
    elif body.what == "channel_profiles":
        res = sim_landscape.import_channel_profiles(db, world.id)
        report.update(res)
        report["imported"] = res["knowledge"]
        summary = (f"Импортировано профилей каналов: {res['knowledge']} "
                   f"(создано каналов: {res['channels']}).")
    elif body.what == "souls":
        report["imported"] = sim_landscape.import_agent_profiles(db, world.id, body.agent_ids or None)
        summary = f"Импортировано душ как агентов полигона: {report['imported']}."
    elif body.what == "missions":
        report["imported"] = sim_landscape.import_missions(db, world.id, body.mission_ids or None)
        summary = f"Импортировано миссий как сценариев полигона: {report['imported']}."
    elif body.what == "history":
        report["imported"] = sim_landscape.import_activity_history(db, world.id, limit=body.limit)
        summary = f"Импортировано исторических реплик роя: {report['imported']}."
    else:
        raise HTTPException(
            status_code=400,
            detail=("what должен быть: knowledge | landscape | channel_profiles | souls | "
                    "missions | history"))

    db.commit()
    log_event(db, world.id, "knowledge", summary, actor_kind="operator", actor_label="оператор",
              detail=report, commit=True)
    report["message"] = summary
    return report


# ── Landscape ──────────────────────────────────────────────────────────────

@router.get("/landscape")
def list_landscape(world_id: Optional[int] = None, db: Session = Depends(get_db),
                   _u: AdminUser = Depends(view_perm)) -> dict[str, Any]:
    world = get_world(db, world_id)
    rows = (db.query(SimLandscapeSource).filter(SimLandscapeSource.world_id == world.id)
            .order_by(SimLandscapeSource.id.desc()).all())
    return {"sources": [landscape_out(s) for s in rows]}


@router.post("/landscape", status_code=201)
def create_landscape(body: LandscapeIn, db: Session = Depends(get_db),
                     _u: AdminUser = Depends(edit_perm)) -> dict[str, Any]:
    world = get_world(db, body.world_id)
    if body.kind not in SIM_LANDSCAPE_KINDS:
        raise HTTPException(status_code=400, detail=f"Тип источника должен быть из {list(SIM_LANDSCAPE_KINDS)}.")
    row = SimLandscapeSource(
        world_id=world.id, kind=body.kind, url=body.url, title=body.title,
        target_channel_id=body.target_channel_id, options=body.options,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return landscape_out(row)


@router.put("/landscape/{source_id}")
def update_landscape(source_id: int, body: LandscapeIn, db: Session = Depends(get_db),
                     _u: AdminUser = Depends(edit_perm)) -> dict[str, Any]:
    row = db.get(SimLandscapeSource, source_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Источник не найден.")
    row.kind, row.url, row.title = body.kind, body.url, body.title
    row.target_channel_id, row.options = body.target_channel_id, body.options
    db.commit()
    db.refresh(row)
    return landscape_out(row)


@router.delete("/landscape/{source_id}")
def delete_landscape(source_id: int, db: Session = Depends(get_db),
                     _u: AdminUser = Depends(edit_perm)) -> dict[str, str]:
    row = db.get(SimLandscapeSource, source_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Источник не найден.")
    db.delete(row)
    db.commit()
    return {"status": "success", "message": f"Источник {source_id} удалён."}


@router.post("/landscape/{source_id}/run")
def run_landscape(source_id: int, db: Session = Depends(get_db),
                  _u: AdminUser = Depends(edit_perm)) -> dict[str, Any]:
    row = db.get(SimLandscapeSource, source_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Источник не найден.")
    opts = row.options or {}
    report = sim_landscape.run_landscape_scrape(
        db, row.world_id, row.kind, row.url,
        limit=int(opts.get("limit", 20)), target_channel_id=row.target_channel_id,
        as_knowledge=bool(opts.get("as_knowledge")), tags=opts.get("tags") or [],
    )
    row.last_run_at = _now()
    row.last_status = report["status"]
    row.last_message = report.get("message") or report.get("status")
    row.items_imported += int(report.get("posts", 0)) + int(report.get("knowledge", 0))
    db.commit()
    log_event(db, row.world_id, "landscape", row.last_message or "Скрапинг выполнен.",
              status="done" if report["status"] == "ok" else "error",
              actor_kind="operator", actor_label="ландшафт",
              channel_id=report.get("channel_id"), detail=report, commit=True)
    return {**report, "source": landscape_out(row)}


@router.get("/import/telegram/agents")
def telegram_import_agents(db: Session = Depends(get_db),
                           _u: AdminUser = Depends(view_perm)) -> dict[str, Any]:
    """
    Production agents whose Telegram session can read a channel for the import.

    A SELECT over production tables, which the isolation contract allows — the
    polygon reads the live system, it never writes to it.
    """
    rows = (
        db.query(AgentProfile.agent_id, AgentProfile.codename)
        .join(SoulAccount, SoulAccount.agent_id == AgentProfile.agent_id)
        .filter(SoulAccount.platform == "telegram",
                SoulAccount.status == "active",
                AgentProfile.status == "active")
        .distinct()
        .all()
    )
    return {"agents": [{"agent_id": a, "codename": c or a} for a, c in rows]}


@router.get("/missions/{mission_id}/validate")
def validate_sim_mission(mission_id: int, deep: bool = True,
                         db: Session = Depends(get_db),
                         _u: AdminUser = Depends(view_perm)) -> dict[str, Any]:
    """
    Same sanity checks the live missions get — the polygon is where a wording is
    supposed to be caught, before it is copied into a real mission.
    """
    mission = db.get(SimMission, mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Миссия не найдена.")
    return mission_validate.validate_mission({
        "goal": mission.goal, "stance": mission.stance,
        "our_side": mission.our_side, "opponent": mission.opponent,
        "key_points": mission.key_points or [], "red_lines": mission.red_lines or [],
        "status": mission.status,
    }, deep=deep)


class TelegramImportIn(BaseModel):
    world_id: Optional[int] = None
    # What the operator actually has: a link to the post they want. A bare channel
    # (@name / t.me/name) still works and takes the newest `post_limit` posts.
    source: str = Field(..., min_length=1)
    target_channel_id: Optional[int] = None
    comment_limit: int = Field(100, ge=0, le=200)
    post_limit: int = Field(10, ge=1, le=50)
    # Optional: pin the reading session. Left empty, any agent with a live Telegram
    # account is used — which session reads is an implementation detail, not a choice
    # the operator should have to make.
    agent_id: Optional[str] = None


def _parse_tg_source(source: str) -> tuple[str, Optional[int]]:
    """
    Split what the operator pasted into ``(channel, post_id)``.

    Accepts `https://t.me/<channel>/<id>`, `t.me/<channel>`, `@channel`, `channel`,
    and the private `t.me/c/<internal>/<id>` form.
    """
    raw = (source or "").strip()
    for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            break
    raw = raw.strip("/")
    if not raw:
        return "", None
    parts = raw.split("/")
    if parts[0] == "c" and len(parts) >= 2:
        post_id = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else None
        return f"-100{parts[1]}", post_id
    channel = parts[0]
    if not channel.startswith("@") and not channel.startswith("-"):
        channel = f"@{channel}"
    post_id = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else None
    return channel, post_id


@router.post("/import/telegram")
def import_telegram(body: TelegramImportIn, db: Session = Depends(get_db),
                    _u: AdminUser = Depends(edit_perm)) -> dict[str, Any]:
    """
    Import real posts together with the REAL comments underneath them.

    Until now the polygon could only take posts (the public `t.me/s/<channel>`
    preview exposes no discussion), so its threads were filled by the operator and
    by our own agents — which cannot exercise the half of the pipeline that reads a
    live crowd: thread mood, per-post tactic, anti-echo, reply targeting. Comments
    live in the linked discussion group and need MTProto, so the read is delegated to
    MYRMIDON's session; DAEDALUS only stores what comes back.

    The operator pastes what they have — a link to the post. `source` accepts
    `https://t.me/<channel>/<id>` (that one post and its thread), a bare channel
    (newest `post_limit` posts), or the private `t.me/c/<internal>/<id>` form.

    Imported comments are `author_kind='external'` / `origin='imported'` so they are
    never confused with the polygon's own agents. Re-running is safe: posts are keyed
    by `external_ref` and comments by their Telegram id.
    """
    world = get_world(db, body.world_id)
    channel_ref, post_id = _parse_tg_source(body.source)
    if not channel_ref:
        raise HTTPException(status_code=400,
                            detail="Не разобрал ссылку. Пример: https://t.me/tashkent_news333/35")

    agent_id = body.agent_id
    if not agent_id:
        row = (db.query(AgentProfile.agent_id)
               .join(SoulAccount, SoulAccount.agent_id == AgentProfile.agent_id)
               .filter(SoulAccount.platform == "telegram",
                       SoulAccount.status == "active",
                       AgentProfile.status == "active")
               .first())
        if row is None:
            raise HTTPException(
                status_code=400,
                detail="Нет ни одного агента с активным Telegram-аккаунтом — читать нечем.")
        agent_id = row[0]

    try:
        with httpx.Client(timeout=300.0) as client:
            resp = client.get(
                f"{MYRMIDON_DEVICE_URL}/api/v1/telegram/{agent_id}/export",
                params={"channel": channel_ref, "post_limit": body.post_limit,
                        "comment_limit": body.comment_limit, "post_id": post_id or 0},
                headers={"X-Internal-Token": INTERNAL_API_TOKEN},
            )
        if resp.status_code == 404:
            raise HTTPException(status_code=404,
                                detail=f"У агента {agent_id} нет активного Telegram-аккаунта.")
        resp.raise_for_status()
        data = resp.json()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"MYRMIDON не отдал выгрузку: {exc}") from exc

    if not (data.get("posts") or []):
        raise HTTPException(
            status_code=404,
            detail=f"По ссылке ничего не нашлось: {channel_ref}"
                   + (f", пост {post_id}" if post_id else "") + ".")

    src = data.get("channel") or {}
    username = src.get("username") or channel_ref
    if not username.startswith("@") and not username.startswith("-"):
        username = f"@{username}"

    # Reuse the operator's chosen channel, else match by username, else create one.
    channel: Optional[SimChannel] = None
    if body.target_channel_id:
        channel = db.get(SimChannel, body.target_channel_id)
        if channel is None:
            raise HTTPException(status_code=404, detail="Целевой канал полигона не найден.")
        _require_in_world(world.id, channel.world_id)
    if channel is None:
        channel = (db.query(SimChannel)
                   .filter(SimChannel.world_id == world.id, SimChannel.username == username)
                   .first())
    if channel is None:
        channel = SimChannel(world_id=world.id, username=username,
                             title=src.get("title") or username,
                             subscribers=src.get("members") or 0,
                             source="imported", external_ref=src.get("chat_id"))
        db.add(channel)
        db.flush()

    posts_new = comments_new = posts_seen = comments_seen = 0
    for p in data.get("posts") or []:
        posts_seen += 1
        ref = p.get("url") or f"{username}/{p.get('id')}"
        post = (db.query(SimPost)
                .filter(SimPost.channel_id == channel.id, SimPost.external_ref == ref)
                .first())
        if post is None:
            post = SimPost(
                channel_id=channel.id, text=p.get("text") or "",
                views=p.get("views") or 0, source="imported", external_ref=ref,
                media=[{"kind": p["media_type"]}] if p.get("media_type") else [],
                published_at=_parse_dt(p.get("date")) or _now(),
            )
            db.add(post)
            db.flush()
            posts_new += 1

        # Telegram id → polygon id, so a reply keeps pointing at its parent.
        #
        # Walk the thread CHRONOLOGICALLY. Telegram returns discussion replies
        # newest-first, so a reply arrives before the comment it answers and the
        # parent is not in the map yet — measured, every nested reply landed as its
        # own top-level comment (2 of 8 on one post). Sorting by message id restores
        # the order; ids grow monotonically within a chat.
        id_map: dict[int, int] = {}
        deferred: list[tuple[SimComment, int]] = []
        thread = sorted(p.get("comments") or [], key=lambda c: c.get("id") or 0)
        for c in thread:
            comments_seen += 1
            tg_id = c.get("id")
            parent_tg = c.get("parent_id")
            existing = (db.query(SimComment)
                        .filter(SimComment.post_id == post.id,
                                SimComment.meta["tg_id"].astext == str(tg_id))
                        .first())
            if existing is not None:
                id_map[tg_id] = existing.id
                # Re-running repairs a tree imported before this fix.
                if existing.parent_id is None and parent_tg:
                    deferred.append((existing, parent_tg))
                continue
            # A parent that is not itself a comment is the channel post forwarded into
            # the discussion group — that is what a top-level comment replies to.
            parent_local = id_map.get(parent_tg) if parent_tg else None
            row = SimComment(
                post_id=post.id, parent_id=parent_local,
                author_kind="external",
                author_label=c.get("author") or "аноним",
                text=c.get("text") or "",
                status="published", origin="imported",
                meta={"tg_id": tg_id, "imported_from": username,
                      "is_our_bot": bool(c.get("is_self"))},
                published_at=_parse_dt(c.get("date")) or _now(),
            )
            db.add(row)
            db.flush()
            id_map[tg_id] = row.id
            if parent_tg and parent_local is None:
                deferred.append((row, parent_tg))
            comments_new += 1

        # Parents that only became resolvable later. One that never resolves is a
        # reply to something outside the imported window — it stays top-level, which
        # is the honest representation of a thread we only partly hold.
        for row, parent_tg in deferred:
            local = id_map.get(parent_tg)
            if local and local != row.id:
                row.parent_id = local

    log_event(db, world.id, "import",
              f"Импорт из {username}: постов {posts_new}/{posts_seen}, "
              f"комментариев реальных людей {comments_new}/{comments_seen}.",
              actor_kind="system")
    db.commit()
    return {
        "status": "ok", "channel_id": channel.id, "channel": channel.username,
        "posts_imported": posts_new, "posts_seen": posts_seen,
        "comments_imported": comments_new, "comments_seen": comments_seen,
    }


@router.post("/landscape/scrape")
def scrape_now(body: ScrapeIn, db: Session = Depends(get_db),
               _u: AdminUser = Depends(edit_perm)) -> dict[str, Any]:
    """Ad-hoc scrape (optionally remembered as a reusable source)."""
    world = get_world(db, body.world_id)
    if body.kind not in ("rss", "web", "tg_preview"):
        raise HTTPException(status_code=400, detail="Тип скрапинга: rss | web | tg_preview.")
    report = sim_landscape.run_landscape_scrape(
        db, world.id, body.kind, body.url, limit=body.limit,
        target_channel_id=body.target_channel_id, as_knowledge=body.as_knowledge,
        tags=body.tags,
    )
    source_row = None
    if body.save_source and report["status"] == "ok":
        source_row = SimLandscapeSource(
            world_id=world.id, kind=body.kind, url=body.url, title=report.get("title"),
            target_channel_id=report.get("channel_id"),
            options={"limit": body.limit, "as_knowledge": body.as_knowledge, "tags": body.tags},
            last_run_at=_now(), last_status=report["status"], last_message=report.get("message"),
            items_imported=int(report.get("posts", 0)) + int(report.get("knowledge", 0)),
        )
        db.add(source_row)
    db.commit()
    log_event(db, world.id, "landscape", report.get("message") or "Скрапинг выполнен.",
              status="done" if report["status"] == "ok" else "error",
              actor_kind="operator", actor_label="ландшафт",
              channel_id=report.get("channel_id"), detail=report, commit=True)
    if source_row is not None:
        db.refresh(source_row)
        report["source"] = landscape_out(source_row)
    return report


# ── Generation ─────────────────────────────────────────────────────────────

@router.post("/generate")
def generate_one(body: GenerateIn, db: Session = Depends(get_db),
                 _u: AdminUser = Depends(edit_perm)) -> dict[str, Any]:
    """
    Generate ONE comment (or reply) from one AI persona, synchronously, and store
    it as draft / generated / published depending on ``mode``. The assembled
    prompt and the retrieved RAG rows come back with the answer.
    """
    if body.mode not in SIM_JOB_MODES:
        raise HTTPException(status_code=400, detail=f"mode должен быть из {list(SIM_JOB_MODES)}.")
    post = db.get(SimPost, body.post_id)
    persona = db.get(SimPersona, body.persona_id)
    if post is None or persona is None:
        raise HTTPException(status_code=404, detail="Пост или агент не найдены.")
    world_id = _post_world_id(db, post)
    _require_in_world(world_id, persona.world_id)

    parent = db.get(SimComment, body.parent_id) if body.parent_id else None
    if parent is not None and parent.post_id != post.id:
        raise HTTPException(status_code=400, detail="Родительский комментарий из другого поста.")
    mission = db.get(SimMission, body.mission_id) if body.mission_id else None

    payload, facts = sim_generator.build_request(
        db, world_id, persona, post,
        mode="reply" if parent is not None else "comment", mission=mission, parent=parent,
        prompt_override=body.prompt_override, tone=body.tone, max_tokens=body.max_tokens,
        temperature=body.temperature, rag_limit=body.rag_limit,
    )
    result = sim_generator.request_generation(payload)
    text = (result.get("text") or "").strip()
    ok = result.get("status") == "ok" and bool(text)

    status_map = {"generate": "generated", "generate_publish": "published", "draft": "draft"}
    comment = SimComment(
        post_id=post.id, parent_id=parent.id if parent else None, author_kind="persona",
        persona_id=persona.id,
        text=text or f"[ошибка генерации: {result.get('reason') or 'пустой ответ'}]",
        status=status_map[body.mode] if ok else "error", origin="ai",
        mission_id=mission.id if mission else None,
        meta={
            "prompt": result.get("prompt", ""), "reason": result.get("reason", ""),
            "guardrail": result.get("guardrail", ""), "model": result.get("model", ""),
            "tactic": result.get("tactic", ""),
            "rag": [{"id": f.id, "title": f.title, "content": f.content[:300]} for f in facts],
            "mode": body.mode,
        },
        published_at=_now() if (ok and body.mode == "generate_publish") else None,
    )
    db.add(comment)
    # File the argument in the mission's shared memory, exactly as MYRMIDON does after
    # a live comment publishes — otherwise the polygon's roster keeps replaying each
    # other and a configuration gets judged on behaviour production no longer has.
    if ok:
        sim_generator.record_said(db, mission.id if mission else None, post.id,
                                  text, persona.agent_key or persona.codename)
    db.commit()
    db.refresh(comment)

    out = comment_out(db, comment)
    log_event(db, world_id, "comment",
              (f"{persona.codename}: {text[:90]}" if ok
               else f"{persona.codename}: ошибка генерации — {result.get('reason', '')[:80]}"),
              status=comment.status, actor_kind="persona", actor_label=persona.codename,
              actor_id=persona.id, post_id=post.id, comment_id=comment.id,
              channel_id=post.channel_id, mission_id=comment.mission_id,
              detail={"reason": result.get("reason", "")}, commit=True)
    return {
        "status": "ok" if ok else "error",
        "reason": result.get("reason", ""),
        "comment": out,
        "prompt": result.get("prompt", ""),
        "rag": [knowledge_out(f) for f in facts],
    }


class GeneratePostIn(BaseModel):
    world_id: Optional[int] = None
    channel_id: int
    persona_id: Optional[int] = None          # автор текста (журналист/очевидец)
    mission_id: Optional[int] = None
    topic: Optional[str] = None               # о чём написать
    kind: str = "post"                        # post | article
    tone: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    prompt_override: Optional[str] = None
    create: bool = True                       # сразу создать пост в канале


@router.post("/generate/post")
def generate_post(body: GeneratePostIn, db: Session = Depends(get_db),
                  _u: AdminUser = Depends(edit_perm)) -> dict[str, Any]:
    """
    Generate a POST (or a longer article) for a simulation channel — the material
    the agents will then argue under. Optionally creates the post immediately.
    """
    channel = db.get(SimChannel, body.channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Канал не найден.")
    world_id = channel.world_id

    persona = db.get(SimPersona, body.persona_id) if body.persona_id else None
    if persona is not None:
        _require_in_world(world_id, persona.world_id)
    mission = db.get(SimMission, body.mission_id) if body.mission_id else None

    topic = (body.topic or "").strip()
    facts = sim_generator.retrieve_knowledge(
        db, world_id, f"{topic} {(mission.goal if mission else '') or ''}", limit=4)
    payload = {
        "mode": "post",
        "kind": body.kind,
        "persona": sim_generator.persona_payload(persona) if persona else {
            "codename": channel.title, "bio": f"редакция канала {channel.username}",
            "style": {"tone_level": 4, "vocab_level": 6, "emoji_frequency": 1, "aggression": 2},
        },
        "mission": sim_generator.mission_payload(mission),
        "channel": sim_generator.channel_payload(channel),
        "post": {"text": topic, "media_context": ""},
        "thread": [],
        "knowledge": [{"title": f.title, "content": f.content, "tags": f.tags or []} for f in facts],
        "rules": sim_generator.system_rules(db, world_id),
        "tone": body.tone or "",
        "max_tokens": body.max_tokens or (600 if body.kind == "article" else None),
        "temperature": body.temperature,
        "prompt_override": body.prompt_override or "",
    }
    result = sim_generator.request_generation(payload)
    text = (result.get("text") or "").strip()
    ok = result.get("status") == "ok" and bool(text)

    post = None
    if ok and body.create:
        post = SimPost(
            channel_id=channel.id, text=text, media=[], reactions={}, views=0,
            source="generated", published_at=_now(),
        )
        db.add(post)
        db.commit()
        db.refresh(post)

    log_event(db, world_id, "post",
              (f"Сгенерирован {'материал' if body.kind == 'article' else 'пост'} в {channel.username}: {text[:70]}"
               if ok else f"Не удалось сгенерировать пост: {result.get('reason', '')[:80]}"),
              status="published" if (ok and post) else ("generated" if ok else "error"),
              actor_kind="persona" if persona else "system",
              actor_label=persona.codename if persona else channel.title,
              channel_id=channel.id, post_id=post.id if post else None, commit=True)

    return {
        "status": "ok" if ok else "error", "reason": result.get("reason", ""),
        "text": text, "prompt": result.get("prompt", ""),
        "post": post_out(db, post) if post else None,
        "rag": [knowledge_out(f) for f in facts],
    }


@router.post("/generate/batch", status_code=201)
def generate_batch(body: BatchIn, db: Session = Depends(get_db),
                   _u: AdminUser = Depends(edit_perm)) -> dict[str, Any]:
    """
    Mass generation: N artefacts from several agents AND several manual accounts
    at once. Runs in the background; poll ``/jobs``. No production rate limits,
    cooldowns or active-hours apply inside the polygon.
    """
    if body.mode not in SIM_JOB_MODES:
        raise HTTPException(status_code=400, detail=f"mode должен быть из {list(SIM_JOB_MODES)}.")
    post = db.get(SimPost, body.post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Пост не найден.")
    if not body.persona_ids and not body.account_ids:
        raise HTTPException(status_code=400, detail="Выберите хотя бы одного агента или аккаунт.")
    if body.account_ids and not body.account_texts:
        raise HTTPException(
            status_code=400,
            detail="Для ручных аккаунтов задайте тексты реплик — аккаунт не управляется ИИ.")
    world_id = _post_world_id(db, post)

    job = SimJob(
        world_id=world_id, kind="comments", mode=body.mode, status="queued",
        post_id=post.id, mission_id=body.mission_id,
        params={
            "count": body.count, "persona_ids": body.persona_ids,
            "account_ids": body.account_ids, "account_texts": body.account_texts,
            "order": body.order, "pace_sec": body.pace_sec, "reply_ratio": body.reply_ratio,
            "tone": body.tone or "", "temperature": body.temperature,
            "max_tokens": body.max_tokens, "prompt_override": body.prompt_override or "",
        },
        total=body.count,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    log_event(db, world_id, "generation",
              f"Массовая генерация: {body.count} реплик "
              f"({len(body.persona_ids)} агентов, {len(body.account_ids)} аккаунтов), режим {body.mode}.",
              status="scheduled", actor_kind="operator", actor_label="оператор",
              post_id=post.id, job_id=job.id, mission_id=body.mission_id, commit=True)
    sim_generator.run_job_async(job.id)
    return job_out(job)


@router.get("/jobs")
def list_jobs(world_id: Optional[int] = None, limit: int = Query(30, ge=1, le=200),
              db: Session = Depends(get_db), _u: AdminUser = Depends(view_perm)) -> dict[str, Any]:
    world = get_world(db, world_id)
    rows = (db.query(SimJob).filter(SimJob.world_id == world.id)
            .order_by(SimJob.id.desc()).limit(limit).all())
    return {"jobs": [job_out(j) for j in rows]}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int, db: Session = Depends(get_db),
               _u: AdminUser = Depends(edit_perm)) -> dict[str, Any]:
    job = db.get(SimJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Задание не найдено.")
    sim_generator.cancel_job(job_id)
    if job.status in ("queued",):
        job.status = "cancelled"
        job.message = "Отменено до старта."
        db.commit()
    return job_out(job)


# ── Activity feed ──────────────────────────────────────────────────────────

@router.get("/events")
def list_events(world_id: Optional[int] = None, kind: Optional[str] = None,
                status: Optional[str] = None, post_id: Optional[int] = None,
                since_id: Optional[int] = None, limit: int = Query(120, ge=1, le=500),
                db: Session = Depends(get_db), _u: AdminUser = Depends(view_perm)) -> dict[str, Any]:
    world = get_world(db, world_id)
    q = db.query(SimEvent).filter(SimEvent.world_id == world.id)
    if kind:
        q = q.filter(SimEvent.kind.in_(kind.split(",")))
    if status:
        q = q.filter(SimEvent.status.in_(status.split(",")))
    if post_id:
        q = q.filter(SimEvent.post_id == post_id)
    if since_id:
        q = q.filter(SimEvent.id > since_id)
    rows = q.order_by(SimEvent.id.desc()).limit(limit).all()
    return {"events": [event_out(e) for e in rows], "world_id": world.id}


@router.delete("/events")
def clear_events(world_id: Optional[int] = None, db: Session = Depends(get_db),
                 _u: AdminUser = Depends(edit_perm)) -> dict[str, str]:
    world = get_world(db, world_id)
    db.query(SimEvent).filter(SimEvent.world_id == world.id).delete()
    db.commit()
    return {"status": "success", "message": "Лента активностей очищена."}


# ── Inspector (raw state of any entity) ────────────────────────────────────

_INSPECTABLE = {
    "world": SimWorld, "channel": SimChannel, "post": SimPost, "comment": SimComment,
    "account": SimAccount, "persona": SimPersona, "mission": SimMission,
    "knowledge": SimKnowledge, "landscape": SimLandscapeSource, "job": SimJob, "event": SimEvent,
}


@router.get("/inspect/{entity}/{entity_id}")
def inspect(entity: str, entity_id: int, db: Session = Depends(get_db),
            _u: AdminUser = Depends(view_perm)) -> dict[str, Any]:
    """Raw column dump of any simulation entity — the right-column inspector."""
    model = _INSPECTABLE.get(entity)
    if model is None:
        raise HTTPException(status_code=400, detail=f"Неизвестная сущность: {entity}")
    row = db.get(model, entity_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Объект не найден.")
    data = {}
    for column in model.__table__.columns:
        value = getattr(row, column.name)
        data[column.name] = value.isoformat() if isinstance(value, datetime) else value
    return {"entity": entity, "id": entity_id, "table": model.__tablename__, "data": data}


@router.get("/health")
def health(db: Session = Depends(get_db), _u: AdminUser = Depends(view_perm)) -> dict[str, Any]:
    """Is the polygon's generation path alive (Redis/ORPHEUS reachable)?"""
    world = get_world(db)
    return {
        "status": "ok",
        "world_id": world.id,
        "engine_available": sim_generator.orpheus_available(),
        "queue": sim_generator.SIM_GEN_QUEUE,
        "isolated": True,
    }
