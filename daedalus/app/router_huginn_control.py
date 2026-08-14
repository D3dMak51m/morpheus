"""
DAEDALUS — HUGINN Control Router
==================================
Administrative endpoints for the HUGINN gathering layer.
"""

from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AdminUser, CapturedEvent
from app.rbac import require_permission

router = APIRouter(prefix="/api/v1/huginn", tags=["Huginn Control"])

class CapturedEventResponse(BaseModel):
    id: int
    event_id: str
    source_platform: str
    source_target: str
    post_id: str
    text_content: Optional[str]
    media_type: Optional[str]
    media_path: Optional[str]
    layers: Optional[dict]
    timestamp: int
    status: str

    class Config:
        from_attributes = True

class CapturedEventUpdateRequest(BaseModel):
    text_content: Optional[str] = None
    layers: Optional[dict] = None
    status: Optional[str] = None

@router.get("/captured-events", response_model=dict[str, Any])
def list_captured_events(
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("monitoring:view")),
) -> dict[str, Any]:
    """View paginated raw stories intercepted by HUGINN."""
    query = db.query(CapturedEvent).order_by(CapturedEvent.timestamp.desc())
    total = query.count()
    events = query.offset(skip).limit(limit).all()
    
    return {
        "events": [CapturedEventResponse.model_validate(e) for e in events],
        "total": total
    }

@router.put("/captured-events/{event_id}", response_model=CapturedEventResponse)
def update_captured_event(
    event_id: int,
    request: CapturedEventUpdateRequest,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("agents:manage")),
) -> CapturedEventResponse:
    """Manual operator override for captured events."""
    event = db.query(CapturedEvent).filter(CapturedEvent.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Captured event not found.")

    if request.text_content is not None:
        event.text_content = request.text_content
    if request.layers is not None:
        event.layers = request.layers
    if request.status is not None:
        event.status = request.status

    db.commit()
    db.refresh(event)
    return CapturedEventResponse.model_validate(event)

@router.post("/force-sync")
def force_sync(
    _user: AdminUser = Depends(require_permission("agents:manage")),
) -> dict[str, str]:
    """Trigger an immediate target landscape update broadcast across the container network."""
    import redis
    import os
    import logging
    
    REDIS_HOST = os.getenv("REDIS_HOST", "redis")
    REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
    
    try:
        client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0)
        # We can implement a PubSub channel in the future. 
        # For now, restarting the Huginn container achieves a force sync.
        import subprocess
        # This is a bit hacky but works for the current architecture without PubSub
        # We're running in Docker, we can't easily restart another container directly
        # Instead we could publish to a Redis channel that Huginn listens to
        client.publish("control:huginn:force_sync", "sync")
        return {"status": "success", "message": "Force sync signal broadcasted to HUGINN."}
    except Exception as e:
        logging.error(f"Failed to force sync: {e}")
        raise HTTPException(status_code=500, detail="Failed to broadcast sync signal.")

from fastapi import Header

from app.router_knowledge import clean_fact_text, is_junk_fact

class RawEventCaptureRequest(BaseModel):
    event_id: str
    source_platform: str
    source_target: str
    post_id: str
    text_content: Optional[str] = None
    media_type: Optional[str] = None
    media_path: Optional[str] = None
    layers: Optional[dict] = None
    timestamp: int

@router.post("/internal/capture")
def capture_internal_event(
    request: RawEventCaptureRequest,
    x_internal_token: str = Header(None),
    db: Session = Depends(get_db),
):
    """Internal endpoint for HUGINN to push real events."""
    if x_internal_token != "morpheus-internal-sync-key":
        raise HTTPException(status_code=403, detail="Invalid internal token")
        
    # Stage 39 — the News Hub shows what the swarm actually read, so it gets the same
    # scrubbing the knowledge base gets. Raw captures used to display the feed's
    # read-more link text and, for sites whose article body never extracts, nothing but
    # a comment widget and a subscription ad.
    text_content = clean_fact_text(request.text_content or "")
    if not text_content or is_junk_fact(text_content):
        return {"status": "skipped", "message": "Boilerplate, not news"}

    # Check if event already exists
    existing = db.query(CapturedEvent).filter(CapturedEvent.event_id == request.event_id).first()
    if existing:
        return {"status": "skipped", "message": "Event already captured"}
        
    event = CapturedEvent(
        event_id=request.event_id,
        source_platform=request.source_platform,
        source_target=request.source_target,
        post_id=request.post_id,
        text_content=text_content,
        media_type=request.media_type,
        media_path=request.media_path,
        layers=request.layers or {},
        timestamp=request.timestamp,
        status="pending"
    )
    db.add(event)
    db.commit()
    return {"status": "success", "event_id": event.event_id}

class InternalStatusUpdateRequest(BaseModel):
    status: str

@router.put("/internal/capture/{event_id}")
def update_internal_event_status(
    event_id: str,
    request: InternalStatusUpdateRequest,
    x_internal_token: str = Header(None),
    db: Session = Depends(get_db),
):
    """Internal endpoint for ORPHEUS to update event status."""
    if x_internal_token != "morpheus-internal-sync-key":
        raise HTTPException(status_code=403, detail="Invalid internal token")
        
    event = db.query(CapturedEvent).filter(CapturedEvent.event_id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
        
    event.status = request.status
    db.commit()
    return {"status": "success"}
