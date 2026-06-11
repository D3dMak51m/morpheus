"""
DAEDALUS — Sandbox Console Router (Stage 16)
==============================================
Operator-driven manual execution suite.

Exposes a single secured endpoint that lets a SuperAdmin trigger an immediate,
isolated physical typing sequence on a specific AVD — bypassing the Redis
event queues entirely. The request is proxied straight to MYRMIDON's internal
Device API, which instantiates the real mobile driver and performs W3C
coordinate-based `human_type()`.

The operator watches the result live via the Sandbox Console's WebVNC panel.
"""

import os
import logging
from typing import Any, Dict

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.models import AdminUser
from app.rbac import require_permission

logger = logging.getLogger("daedalus.sandbox")

router = APIRouter(prefix="/api/v1/sandbox", tags=["Sandbox Console"])

MYRMIDON_DEVICE_URL = os.getenv("MYRMIDON_DEVICE_URL", "http://myrmidon:8003")
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "morpheus-internal-sync-key")


class SandboxExecuteRequest(BaseModel):
    """Strict payload for a manual sandbox typing run."""
    agent_id: str = Field(..., description="Persona whose driver/credentials to instantiate")
    device_id: str = Field(..., description="Target AVD identifier, e.g. 'localhost:5555'")
    target_app: str = Field("base", description="Target app context: instagram | telegram | base")
    text_payload: str = Field(..., min_length=1, description="Text to physically type on the device")


@router.post("/execute")
def sandbox_execute(
    request: SandboxExecuteRequest,
    _user: AdminUser = Depends(require_permission("agents:manage")),
) -> Dict[str, Any]:
    """
    Proxy an immediate, isolated typing sequence to MYRMIDON's Device API.

    This bypasses the normal HUGINN → ORPHEUS → Redis → MYRMIDON pipeline and
    drives the physical keyboard directly, returning a definitive success /
    failure log for the Sandbox Console terminal.
    """
    target_url = (
        f"{MYRMIDON_DEVICE_URL}/api/v1/devices/{request.device_id}/sandbox-execute"
    )
    logger.info(
        "Sandbox execute → agent=%s device=%s app=%s (%d chars)",
        request.agent_id, request.device_id, request.target_app, len(request.text_payload),
    )
    try:
        # Generous timeout — a real Appium typing run can take tens of seconds.
        with httpx.Client(timeout=180.0) as client:
            resp = client.post(
                target_url,
                json={
                    "agent_id": request.agent_id,
                    "target_app": request.target_app,
                    "text_payload": request.text_payload,
                },
                headers={"X-Internal-Token": INTERNAL_API_TOKEN},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        detail = e.response.text if e.response is not None else str(e)
        logger.error("Sandbox proxy returned HTTP error: %s", detail)
        raise HTTPException(status_code=502, detail=f"MYRMIDON rejected sandbox run: {detail}")
    except Exception as e:
        logger.error("Sandbox proxy failed to reach MYRMIDON: %s", e)
        raise HTTPException(status_code=502, detail=f"Failed to reach MYRMIDON Device API: {e}")
