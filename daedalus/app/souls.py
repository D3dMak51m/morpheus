"""
DAEDALUS — Agent Souls / Profiles CRUD Router
=================================================
Manages the full psychological profiles of AI agent personas.
ORPHEUS fetches profiles from /api/v1/internal/profiles instead of YAML.
"""

import os
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Header, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AdminUser, AgentProfile, SoulAccount, VirtualDevice, AccountAuditLog, ProfileHistory, LANDSCAPE_LAYERS
from app.rbac import require_permission

router = APIRouter(prefix="/api/v1/souls", tags=["Agent Souls"])

INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "morpheus-internal-sync-key")


# ── Strict Persona Sub-Schemas (Visual Persona Builder, Stage 16) ─────────
# These rigorous models replace the raw <textarea> JSON inputs in Daedalus.
# The frontend Visual Persona Builder compiles its sliders / rule-arrays into
# these exact shapes, so malformed human-typed JSON can never reach the DB.

class CommunicationStyle(BaseModel):
    """
    Structured psychological communication profile.
    All numeric attributes are discrete 1-10 scales driven by UI sliders.
    `quirks` is a clean list of free-text typing/speech idiosyncrasies.
    """
    model_config = ConfigDict(extra="forbid")

    tone_level: int = Field(5, ge=1, le=10, description="Formal (1) → Casual (10)")
    vocab_level: int = Field(5, ge=1, le=10, description="Simple (1) → Erudite (10)")
    emoji_frequency: int = Field(3, ge=1, le=10, description="None (1) → Heavy (10)")
    aggression: int = Field(3, ge=1, le=10, description="Passive (1) → Combative (10)")
    quirks: list[str] = Field(default_factory=list, description="Typing/speech idiosyncrasies")


class BehavioralRules(BaseModel):
    """
    Operational guardrails for an agent.
    `rules` is the visual, validated list of natural-language directives.
    The numeric fields preserve the operational contract consumed by ORPHEUS
    (e.g. `min_delay_between_posts_sec`) so downstream execution never breaks.
    """
    model_config = ConfigDict(extra="allow")

    rules: list[str] = Field(default_factory=list, description="Natural-language behavioral directives")
    min_delay_between_posts_sec: int = Field(45, ge=0, description="Minimum cooldown between actions")
    max_posts_per_hour: int = Field(5, ge=0, description="Hard rate limit per hour")


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
    communication_style: CommunicationStyle = Field(default_factory=CommunicationStyle)
    behavioral_rules: BehavioralRules = Field(default_factory=BehavioralRules)
    platforms: Optional[list[str]] = []
    layers_affinity: Optional[dict] = {}
    # Stage 21 — RAG layer subscriptions (defaults to global-only).
    context_subscriptions: Optional[list[str]] = Field(default_factory=lambda: ["global"])
    active_hours_start: int = 8
    active_hours_end: int = 22
    core_mission: Optional[str] = None
    current_stance_modifiers: Optional[dict] = {}

    @field_validator("context_subscriptions")
    @classmethod
    def _valid_subscriptions(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return v
        cleaned = [s.strip().lower() for s in v if s]
        invalid = set(cleaned) - set(LANDSCAPE_LAYERS)
        if invalid:
            raise ValueError(f"Invalid context_subscriptions {sorted(invalid)}. Allowed: {list(LANDSCAPE_LAYERS)}")
        return cleaned


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
    communication_style: Optional[CommunicationStyle] = None
    behavioral_rules: Optional[BehavioralRules] = None
    platforms: Optional[list[str]] = None
    layers_affinity: Optional[dict] = None
    context_subscriptions: Optional[list[str]] = None
    active_hours_start: Optional[int] = None
    active_hours_end: Optional[int] = None
    core_mission: Optional[str] = None
    current_stance_modifiers: Optional[dict] = None

    @field_validator("context_subscriptions")
    @classmethod
    def _valid_subscriptions(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return v
        cleaned = [s.strip().lower() for s in v if s]
        invalid = set(cleaned) - set(LANDSCAPE_LAYERS)
        if invalid:
            raise ValueError(f"Invalid context_subscriptions {sorted(invalid)}. Allowed: {list(LANDSCAPE_LAYERS)}")
        return cleaned


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
    context_subscriptions: Optional[list]
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

    # Save old profile to history before updating
    old_profile_data = {
        c.name: getattr(profile, c.name) for c in profile.__table__.columns
        if c.name not in ["id", "created_at", "updated_at"]
    }
    history = ProfileHistory(agent_id=agent_id, profile_data=old_profile_data)
    db.add(history)

    update_data = request.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if value is not None:
            setattr(profile, field, value)

    db.commit()
    db.refresh(profile)
    return ProfileResponse.model_validate(profile)

@router.post("/profiles/{agent_id}/rollback/{history_id}")
def rollback_profile(
    agent_id: str,
    history_id: int,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("agents:manage"))
):
    profile = db.query(AgentProfile).filter(AgentProfile.agent_id == agent_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail=f"Profile for agent '{agent_id}' not found.")
    
    history = db.query(ProfileHistory).filter(ProfileHistory.id == history_id, ProfileHistory.agent_id == agent_id).first()
    if not history:
        raise HTTPException(status_code=404, detail="History record not found")

    # Save current state to history before rolling back
    current_data = {
        c.name: getattr(profile, c.name) for c in profile.__table__.columns
        if c.name not in ["id", "created_at", "updated_at"]
    }
    new_history = ProfileHistory(agent_id=agent_id, profile_data=current_data)
    db.add(new_history)

    # Restore from history record
    for field, value in history.profile_data.items():
        if hasattr(profile, field) and field not in ["id", "agent_id"]:
            setattr(profile, field, value)
    
    db.commit()
    return {"status": "success", "message": "Profile rolled back"}

class ProfileHistoryResponse(BaseModel):
    id: int
    agent_id: str
    profile_data: dict
    created_at: Any

    class Config:
        from_attributes = True

@router.get("/profiles/{agent_id}/history", response_model=list[ProfileHistoryResponse])
def get_profile_history(
    agent_id: str,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("agents:manage"))
):
    return db.query(ProfileHistory).filter(ProfileHistory.agent_id == agent_id).order_by(ProfileHistory.id.desc()).all()


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
            "context_subscriptions": p.context_subscriptions or ["global"],
            "active_hours_start": p.active_hours_start,
            "active_hours_end": p.active_hours_end,
            "core_mission": p.core_mission,
            "current_stance_modifiers": p.current_stance_modifiers or {},
            "execution_delay": (p.behavioral_rules or {}).get("min_delay_between_posts_sec", 45),
        }

    return {"profiles": result, "total": len(result)}
@router.post("/genesis", response_model=ProfileResponse)
def generate_genesis_profile(
    seed: dict,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("agents:manage"))
):
    """
    Generate a new agent profile via the Genesis Engine.
    """
    from app.genesis_engine import generate_profile, GenesisSeed
    
    try:
        # Validate input seed
        seed_model = GenesisSeed(**seed)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid seed data: {e}")

    # Check if agent_id already exists
    existing = db.query(AgentProfile).filter(AgentProfile.agent_id == seed_model.agent_id).first()
    if existing:
        raise HTTPException(status_code=400, detail="Agent profile already exists with this ID.")

    # Generate the profile
    profile_data = generate_profile(seed_model)

    # Save to database
    new_profile = AgentProfile(**profile_data)
    db.add(new_profile)
    db.commit()
    db.refresh(new_profile)

    return new_profile

# ── Accounts & Virtual Devices ─────────────────────────────────────────────

class AccountResponse(BaseModel):
    id: int
    agent_id: Optional[str]
    platform: str
    username: str
    status: str
    device_id: Optional[str]

    class Config:
        from_attributes = True

@router.get("/accounts", response_model=list[AccountResponse])
def get_accounts(db: Session = Depends(get_db)):
    accounts = db.query(SoulAccount).all()
    return accounts

@router.put("/accounts/{account_id}/assign")
def assign_account(
    account_id: int,
    agent_id: Optional[str] = None,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("agents:manage"))
):
    account = db.query(SoulAccount).filter(SoulAccount.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    old_agent = account.agent_id
    account.agent_id = agent_id
    db.commit()

    # Log
    action = f"Unassigned from {old_agent}" if not agent_id else f"Assigned to {agent_id}"
    log = AccountAuditLog(account_id=account.id, action=action)
    db.add(log)
    db.commit()

    return {"status": "success", "agent_id": account.agent_id}

class AccountAuditLogResponse(BaseModel):
    id: int
    account_id: int
    action: str
    details: Optional[str]
    timestamp: Any

    class Config:
        from_attributes = True

@router.get("/accounts/{account_id}/history", response_model=list[AccountAuditLogResponse])
def get_account_history(
    account_id: int,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("agents:manage"))
):
    return db.query(AccountAuditLog).filter(AccountAuditLog.account_id == account_id).order_by(AccountAuditLog.id.desc()).all()

class DeviceResponse(BaseModel):
    id: int
    device_id: str
    assigned_agent_id: Optional[str]
    status: str

    class Config:
        from_attributes = True

@router.get("/devices", response_model=list[DeviceResponse])
def get_devices(db: Session = Depends(get_db)):
    return db.query(VirtualDevice).all()

@router.post("/devices")
def create_device(
    device_id: str,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("agents:manage"))
):
    if db.query(VirtualDevice).filter(VirtualDevice.device_id == device_id).first():
        raise HTTPException(status_code=400, detail="Device already exists")
    
    dev = VirtualDevice(device_id=device_id)
    db.add(dev)
    db.commit()
    return {"status": "success"}

@router.put("/devices/{id}/assign")
def assign_device(
    id: int,
    agent_id: Optional[str] = None,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("agents:manage"))
):
    dev = db.query(VirtualDevice).filter(VirtualDevice.id == id).first()
    if not dev:
        raise HTTPException(status_code=404, detail="Device not found")
    dev.assigned_agent_id = agent_id
    db.commit()
    return {"status": "success"}

@router.delete("/devices/{id}")
def delete_device(
    id: int,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("agents:manage"))
):
    dev = db.query(VirtualDevice).filter(VirtualDevice.id == id).first()
    if not dev:
        raise HTTPException(status_code=404, detail="Device not found")
    db.delete(dev)
    db.commit()
    return {"status": "success"}


class DeviceStatusUpdateRequest(BaseModel):
    device_id: str
    status: str


@router.post("/internal/device-status")
def internal_update_device_status(
    request: DeviceStatusUpdateRequest,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Stage 19 — Internal hook for MYRMIDON's self-healing daemon to update a
    VirtualDevice lifecycle state (e.g. 'RECOVERING' → 'ONLINE'). Token-secured.
    Auto-registers the device row if the orchestrator healed a device that was
    never explicitly enrolled into the Daedalus fleet.
    """
    if x_internal_token != INTERNAL_API_TOKEN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid internal token.")

    dev = db.query(VirtualDevice).filter(VirtualDevice.device_id == request.device_id).first()
    if not dev:
        dev = VirtualDevice(device_id=request.device_id, status=request.status)
        db.add(dev)
    else:
        dev.status = request.status
    db.commit()
    return {"status": "success", "device_id": request.device_id, "new_status": request.status}
