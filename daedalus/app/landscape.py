"""
DAEDALUS — Scraping Landscape CRUD Router
============================================
Manages the dynamic scraping target registry.
HUGINN fetches active targets from /api/v1/internal/sync-targets
instead of using hardcoded channel lists.
"""

import os
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Header, status
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AdminUser, ScrapingLandscape, LANDSCAPE_LAYERS
from app.rbac import require_permission

router = APIRouter(prefix="/api/v1/landscape", tags=["Scraping Landscape"])

INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "morpheus-internal-sync-key")


# ── Pydantic schemas ─────────────────────────────────────────────────────

def _clean_layers(values: list[str]) -> list[str]:
    """Lowercase, validate against LANDSCAPE_LAYERS, dedupe (order-preserving)."""
    cleaned: list[str] = []
    for raw in values or []:
        v = (raw or "").strip().lower()
        if not v:
            continue
        if v not in LANDSCAPE_LAYERS:
            raise ValueError(f"Invalid layer '{v}'. Allowed: {list(LANDSCAPE_LAYERS)}")
        if v not in cleaned:
            cleaned.append(v)
    return cleaned


class LandscapeCreateRequest(BaseModel):
    platform: str
    type: str = "channel"
    target_identifier: str
    is_active: bool = True
    associated_tags: Optional[list[str]] = []
    # Stage 22 — mandatory default layers (≥1) seeded onto scraped facts.
    default_layers: list[str]

    @field_validator("default_layers")
    @classmethod
    def _valid_layers(cls, v: list[str]) -> list[str]:
        cleaned = _clean_layers(v)
        if not cleaned:
            raise ValueError(f"default_layers must contain at least one of {list(LANDSCAPE_LAYERS)}")
        return cleaned


class LandscapeUpdateRequest(BaseModel):
    platform: Optional[str] = None
    type: Optional[str] = None
    target_identifier: Optional[str] = None
    is_active: Optional[bool] = None
    associated_tags: Optional[list[str]] = None
    default_layers: Optional[list[str]] = None

    @field_validator("default_layers")
    @classmethod
    def _valid_layers(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return v
        cleaned = _clean_layers(v)
        if not cleaned:
            raise ValueError(f"default_layers must contain at least one of {list(LANDSCAPE_LAYERS)}")
        return cleaned


class LandscapeResponse(BaseModel):
    id: int
    platform: str
    type: str
    target_identifier: str
    is_active: bool
    associated_tags: Optional[list[str]]
    default_layers: list[str]

    class Config:
        from_attributes = True


# ── CRUD Endpoints ────────────────────────────────────────────────────────

@router.get("/", response_model=list[LandscapeResponse])
def list_targets(
    platform: Optional[str] = None,
    is_active: Optional[bool] = None,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("agents:view")),
) -> list[LandscapeResponse]:
    """List all scraping targets with optional filters."""
    query = db.query(ScrapingLandscape)
    if platform:
        query = query.filter(ScrapingLandscape.platform == platform)
    if is_active is not None:
        query = query.filter(ScrapingLandscape.is_active == is_active)
    return [LandscapeResponse.model_validate(t) for t in query.all()]


@router.post("/", response_model=LandscapeResponse, status_code=201)
def create_target(
    request: LandscapeCreateRequest,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("agents:manage")),
) -> LandscapeResponse:
    """Add a new scraping target."""
    existing = db.query(ScrapingLandscape).filter(
        ScrapingLandscape.target_identifier == request.target_identifier
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Target already exists.")

    target = ScrapingLandscape(
        platform=request.platform,
        type=request.type,
        target_identifier=request.target_identifier,
        is_active=request.is_active,
        associated_tags=request.associated_tags,
        default_layers=request.default_layers,
    )
    db.add(target)
    db.commit()
    db.refresh(target)
    return LandscapeResponse.model_validate(target)


@router.put("/{target_id}", response_model=LandscapeResponse)
def update_target(
    target_id: int,
    request: LandscapeUpdateRequest,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("agents:manage")),
) -> LandscapeResponse:
    """Update an existing scraping target."""
    target = db.query(ScrapingLandscape).filter(ScrapingLandscape.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found.")

    if request.platform is not None:
        target.platform = request.platform
    if request.type is not None:
        target.type = request.type
    if request.target_identifier is not None:
        target.target_identifier = request.target_identifier
    if request.is_active is not None:
        target.is_active = request.is_active
    if request.associated_tags is not None:
        target.associated_tags = request.associated_tags
    if request.default_layers is not None:
        target.default_layers = request.default_layers

    db.commit()
    db.refresh(target)
    return LandscapeResponse.model_validate(target)


@router.delete("/{target_id}")
def delete_target(
    target_id: int,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("agents:manage")),
) -> dict[str, str]:
    """Remove a scraping target."""
    target = db.query(ScrapingLandscape).filter(ScrapingLandscape.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found.")

    db.delete(target)
    db.commit()
    return {"status": "success", "message": f"Target '{target.target_identifier}' deleted."}


# ── Internal sync endpoint for HUGINN ─────────────────────────────────────

@router.get("/internal/sync-targets")
def sync_targets(
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Lightweight endpoint returning active targets grouped by platform.
    Secured via internal token header. No JWT required.
    """
    if x_internal_token != INTERNAL_API_TOKEN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid internal token.")

    targets = db.query(ScrapingLandscape).filter(ScrapingLandscape.is_active == True).all()

    grouped: dict[str, list[dict[str, str]]] = {}
    for t in targets:
        grouped.setdefault(t.platform, []).append({
            "target_identifier": t.target_identifier,
            "type": t.type,
            "default_layers": t.default_layers or ["global"],
        })

    return {"targets": grouped}
