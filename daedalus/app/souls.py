"""
DAEDALUS — Agent Souls / Profiles CRUD Router
=================================================
Manages the full psychological profiles of AI agent personas.
ORPHEUS fetches profiles from /api/v1/internal/profiles instead of YAML.
"""

import os
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Header, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AdminUser, AgentProfile
from app.rbac import require_permission

router = APIRouter(prefix="/api/v1/souls", tags=["Agent Souls"])

INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "morpheus-internal-sync-key")


# ── Pydantic schemas ─────────────────────────────────────────────────────

class ProfileCreateRequest(BaseModel):
    agent_id: str
    codename: str
    caste: str = "alpha"
    full_name: str
    birth_date: Optional[str] = None
    residence_city: Optional[str] = None
    residence_state: Optional[str] = None
    nationality: Optional[str] = None
    profession: Optional[str] = None
    education: Optional[str] = None
    spoken_languages: Optional[list[str]] = []
    core_interests: Optional[list[str]] = []
    communication_style: Optional[dict] = {}
    behavioral_rules: Optional[dict] = {}
    platforms: Optional[list[str]] = []
    layers_affinity: Optional[dict] = {}
    active_hours_start: int = 8
    active_hours_end: int = 22
    core_mission: Optional[str] = None
    current_stance_modifiers: Optional[dict] = {}


class ProfileUpdateRequest(BaseModel):
    codename: Optional[str] = None
    caste: Optional[str] = None
    full_name: Optional[str] = None
    birth_date: Optional[str] = None
    residence_city: Optional[str] = None
    residence_state: Optional[str] = None
    nationality: Optional[str] = None
    profession: Optional[str] = None
    education: Optional[str] = None
    spoken_languages: Optional[list[str]] = None
    core_interests: Optional[list[str]] = None
    communication_style: Optional[dict] = None
    behavioral_rules: Optional[dict] = None
    platforms: Optional[list[str]] = None
    layers_affinity: Optional[dict] = None
    active_hours_start: Optional[int] = None
    active_hours_end: Optional[int] = None
    core_mission: Optional[str] = None
    current_stance_modifiers: Optional[dict] = None


class ProfileResponse(BaseModel):
    id: int
    agent_id: str
    codename: str
    caste: str
    full_name: str
    birth_date: Optional[str]
    residence_city: Optional[str]
    residence_state: Optional[str]
    nationality: Optional[str]
    profession: Optional[str]
    education: Optional[str]
    spoken_languages: Optional[list]
    core_interests: Optional[list]
    communication_style: Optional[dict]
    behavioral_rules: Optional[dict]
    platforms: Optional[list]
    layers_affinity: Optional[dict]
    active_hours_start: int
    active_hours_end: int
    core_mission: Optional[str]
    current_stance_modifiers: Optional[dict]

    class Config:
        from_attributes = True


# ── CRUD Endpoints ────────────────────────────────────────────────────────

@router.get("/profiles", response_model=list[ProfileResponse])
def list_profiles(
    caste: Optional[str] = None,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("agents:view")),
) -> list[ProfileResponse]:
    """List all agent profiles with optional caste filter."""
    query = db.query(AgentProfile)
    if caste:
        query = query.filter(AgentProfile.caste == caste)
    return [ProfileResponse.model_validate(p) for p in query.all()]


@router.get("/profiles/{agent_id}", response_model=ProfileResponse)
def get_profile(
    agent_id: str,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("agents:view")),
) -> ProfileResponse:
    """Get a single agent profile by agent_id."""
    profile = db.query(AgentProfile).filter(AgentProfile.agent_id == agent_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail=f"Profile for agent '{agent_id}' not found.")
    return ProfileResponse.model_validate(profile)


@router.post("/profiles", response_model=ProfileResponse, status_code=201)
def create_profile(
    request: ProfileCreateRequest,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("agents:manage")),
) -> ProfileResponse:
    """Create a new agent psychological profile."""
    existing = db.query(AgentProfile).filter(AgentProfile.agent_id == request.agent_id).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Profile for agent '{request.agent_id}' already exists.")

    profile = AgentProfile(**request.model_dump())
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return ProfileResponse.model_validate(profile)


@router.put("/profiles/{agent_id}", response_model=ProfileResponse)
def update_profile(
    agent_id: str,
    request: ProfileUpdateRequest,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("agents:manage")),
) -> ProfileResponse:
    """Update an existing agent profile. Only non-null fields are updated."""
    profile = db.query(AgentProfile).filter(AgentProfile.agent_id == agent_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail=f"Profile for agent '{agent_id}' not found.")

    update_data = request.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if value is not None:
            setattr(profile, field, value)

    db.commit()
    db.refresh(profile)
    return ProfileResponse.model_validate(profile)


@router.delete("/profiles/{agent_id}")
def delete_profile(
    agent_id: str,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("agents:manage")),
) -> dict[str, str]:
    """Delete an agent profile."""
    profile = db.query(AgentProfile).filter(AgentProfile.agent_id == agent_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail=f"Profile for agent '{agent_id}' not found.")

    db.delete(profile)
    db.commit()
    return {"status": "success", "message": f"Profile for agent '{agent_id}' deleted."}


# ── Internal endpoint for ORPHEUS ─────────────────────────────────────────

@router.get("/internal/profiles")
def internal_list_profiles(
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Returns all agent profiles keyed by agent_id.
    Used by ORPHEUS PersonaEngine to replace static YAML loading.
    Secured via internal token header.
    """
    if x_internal_token != INTERNAL_API_TOKEN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid internal token.")

    profiles = db.query(AgentProfile).all()
    result = {}
    for p in profiles:
        result[p.agent_id] = {
            "agent_id": p.agent_id,
            "codename": p.codename,
            "caste": p.caste,
            "name": p.full_name,
            "identity": {
                "full_name": p.full_name,
                "birth_date": p.birth_date,
                "city": p.residence_city,
                "state": p.residence_state,
                "nationality": p.nationality,
                "occupation": p.profession,
                "education": p.education,
            },
            "personality": p.communication_style or {},
            "interests": p.core_interests or [],
            "spoken_languages": p.spoken_languages or [],
            "behavioral_rules": p.behavioral_rules or {},
            "platforms": p.platforms or [],
            "layers_affinity": p.layers_affinity or {},
            "active_hours_start": p.active_hours_start,
            "active_hours_end": p.active_hours_end,
            "core_mission": p.core_mission,
            "current_stance_modifiers": p.current_stance_modifiers or {},
            "execution_delay": (p.behavioral_rules or {}).get("min_delay_between_posts_sec", 45),
        }

    return {"profiles": result, "total": len(result)}
