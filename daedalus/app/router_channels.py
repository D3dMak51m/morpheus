"""
DAEDALUS — Channel Profiles Router (internal)
===============================================
Internal endpoints (X-Internal-Token) the swarm uses to build and read per-channel
profiles. MYRMIDON supplies the posts (it has the session); DAEDALUS runs the LLM
(`channel_profiler`) and owns the `channel_profiles` table. See CHANNEL_PROFILING.md.

  POST /channels/internal/profile  — heavy profile (geo/topics/summary/…)
  POST /channels/internal/themes   — light "hot themes now" refresh
  GET  /channels/internal/profile  — fetch the cached profile (for relevance/comments)
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import channel_profiler
from app.database import get_db
from app.models import AdminUser, ChannelProfile
from app.rbac import require_permission

logger = logging.getLogger("daedalus.router_channels")

INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "morpheus-internal-sync-key")

router = APIRouter(prefix="/api/v1/channels", tags=["Channel Profiles"])


def _check_token(token: Optional[str]) -> None:
    if token != INTERNAL_API_TOKEN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid internal token.")


def _norm_ref(ref: str) -> str:
    """Normalize a channel ref so the same channel maps to one profile row."""
    r = (ref or "").strip()
    if r.startswith("@") or (r and not r.lstrip("-").isdigit() and "://" not in r):
        return r.lower()
    return r


def _get(db: Session, platform: str, channel_ref: str) -> Optional[ChannelProfile]:
    return db.query(ChannelProfile).filter(
        ChannelProfile.platform == platform,
        ChannelProfile.channel_ref == _norm_ref(channel_ref),
    ).first()


def _serialize(p: ChannelProfile) -> dict[str, Any]:
    return {
        "platform": p.platform, "channel_ref": p.channel_ref, "title": p.title,
        "geo_layers": p.geo_layers or [], "geo_label": p.geo_label,
        "topics": p.topics or [], "tags": p.tags or [],
        "recent_themes": p.recent_themes or [], "summary": p.summary,
        "audience_tone": p.audience_tone, "language": p.language,
        "sample_count": p.sample_count, "posts_seen": p.posts_seen,
        "last_profiled_at": p.last_profiled_at.isoformat() if p.last_profiled_at else None,
        "last_themes_at": p.last_themes_at.isoformat() if p.last_themes_at else None,
    }


class ProfileBuildRequest(BaseModel):
    platform: str = Field("telegram")
    channel_ref: str = Field(..., min_length=1)
    title: Optional[str] = None
    posts: list[str] = Field(default_factory=list)


class ThemesRequest(BaseModel):
    platform: str = Field("telegram")
    channel_ref: str = Field(..., min_length=1)
    posts: list[str] = Field(default_factory=list)


@router.post("/internal/profile")
async def build_profile(
    request: ProfileBuildRequest,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Heavy profile build/update from a sample of the channel's posts."""
    _check_token(x_internal_token)
    prof = await channel_profiler.profile_channel(request.title or "", request.posts)
    themes = await channel_profiler.extract_themes(request.posts)  # seed hot themes too

    ref = _norm_ref(request.channel_ref)
    row = _get(db, request.platform, ref)
    now = datetime.now(timezone.utc)
    if row is None:
        row = ChannelProfile(platform=request.platform, channel_ref=ref)
        db.add(row)
    if request.title:
        row.title = request.title[:300]
    # Only overwrite heavy fields when the LLM returned something useful.
    if prof.get("geo_layers"):
        row.geo_layers = prof["geo_layers"]
    if prof.get("geo_label"):
        row.geo_label = prof["geo_label"]
    if prof.get("topics"):
        row.topics = prof["topics"]
    if prof.get("tags"):
        row.tags = prof["tags"]
    if prof.get("summary"):
        row.summary = prof["summary"]
    if prof.get("audience_tone"):
        row.audience_tone = prof["audience_tone"]
    if prof.get("language"):
        row.language = prof["language"]
    if themes:
        row.recent_themes = themes
        row.last_themes_at = now
    row.sample_count = len([p for p in request.posts if (p or "").strip()])
    row.posts_seen = (row.posts_seen or 0) + row.sample_count
    row.last_profiled_at = now
    db.commit()
    db.refresh(row)
    logger.info("Profile built: %s/%s geo=%s topics=%s", row.platform, row.channel_ref,
                row.geo_layers, row.topics)
    return {"status": "ok", "profile": _serialize(row)}


@router.post("/internal/themes")
async def refresh_themes(
    request: ThemesRequest,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Light 'hot themes now' refresh (cheap, frequent)."""
    _check_token(x_internal_token)
    themes = await channel_profiler.extract_themes(request.posts)
    ref = _norm_ref(request.channel_ref)
    row = _get(db, request.platform, ref)
    if row is None:
        # No heavy profile yet — create a stub so themes aren't lost.
        row = ChannelProfile(platform=request.platform, channel_ref=ref)
        db.add(row)
    if themes:
        row.recent_themes = themes
    row.last_themes_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return {"status": "ok", "recent_themes": row.recent_themes or []}


@router.get("/profiles")
def list_profiles(
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("monitoring:view")),
) -> dict[str, Any]:
    """Operator-facing: list all channel profiles (newest-profiled first)."""
    rows = (
        db.query(ChannelProfile)
        .order_by(ChannelProfile.last_profiled_at.desc().nullslast())
        .all()
    )
    return {"profiles": [_serialize(p) for p in rows], "total": len(rows)}


@router.get("/internal/profile")
def get_profile(
    platform: str = "telegram",
    channel_ref: str = "",
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Fetch the cached profile (used by the relevance/comment path)."""
    _check_token(x_internal_token)
    row = _get(db, platform, channel_ref)
    if row is None:
        return {"status": "missing"}
    return {"status": "ok", "profile": _serialize(row)}
