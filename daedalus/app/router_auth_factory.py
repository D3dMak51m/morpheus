"""
DAEDALUS — Auth Factory
=======================
Interactive state machine for platform onboarding.
Supports Pyrogram OTP handshake for Telegram and generic session import for mobile apps.
"""

import logging
import os
from typing import Dict, Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from sqlalchemy.orm import Session
from pyrogram import Client

from app.database import get_db
from app.models import SoulAccount, AgentProfile
from app.rbac import require_permission, AdminUser

logger = logging.getLogger("daedalus.auth_factory")

router = APIRouter(prefix="/api/v1/auth-factory", tags=["Auth Factory"])

TG_API_ID = os.getenv("TG_API_ID", "2040")
TG_API_HASH = os.getenv("TG_API_HASH", "b18441a1ff607e10a989891a5462e627")
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "morpheus-internal-sync-key")
MYRMIDON_DEVICE_URL = os.getenv("MYRMIDON_DEVICE_URL", "http://myrmidon:8003")

# Default app package names per platform for native shared_prefs extraction.
PLATFORM_PACKAGES = {
    "instagram": "com.instagram.android",
    "twitter": "com.twitter.android",
    "threads": "com.instagram.barcelona",
    "youtube": "com.google.android.youtube",
    "facebook": "com.facebook.katana",
}

# Global dictionary to retain live Pyrogram clients in memory between REST calls
ACTIVE_SESSIONS: dict[str, Client] = {}

# ── Schemas ─────────────────────────────────────────────────────────────

class PhoneRequest(BaseModel):
    phone_number: str

class CodeVerificationRequest(BaseModel):
    phone_number: str
    phone_code_hash: str
    code: str
    agent_id: str
    device_id: str
    password: str | None = None

class MobileImportRequest(BaseModel):
    platform: str
    agent_id: str
    device_id: str
    username: str
    session_payload: Dict[str, Any]

class MobileExtractRequest(BaseModel):
    platform: str
    device_id: str
    username: str
    # Optional: bind the resulting account to a soul immediately. If omitted the
    # account is created floating ('unbound') for later binding.
    agent_id: Optional[str] = None
    # Optional override of the native package for shared_prefs extraction.
    package: Optional[str] = None

class AuthResponse(BaseModel):
    status: str
    phone_code_hash: str | None = None
    message: str

# ── Endpoints ───────────────────────────────────────────────────────────

def verify_token_or_permission(
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
    # we can't easily do OR logic with Depends without a custom class/function that catches exceptions.
    # We will use x_internal_token directly in endpoints or just require permission if token is missing.
):
    pass

async def get_auth(
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
):
    if x_internal_token == INTERNAL_API_TOKEN:
        return True
    raise HTTPException(status_code=403, detail="Not authorized")

@router.post("/telegram/request-code", response_model=AuthResponse)
async def request_tg_code(
    params: PhoneRequest,
):
    """
    Step 1: Initialize Pyrogram client, request OTP code via Telegram.
    """
    phone_number = params.phone_number
    
    if phone_number in ACTIVE_SESSIONS:
        # Clean up existing session if it exists
        try:
            await ACTIVE_SESSIONS[phone_number].disconnect()
        except Exception:
            pass
        del ACTIVE_SESSIONS[phone_number]
        
    client = Client(
        f":memory:{phone_number}",
        api_id=TG_API_ID,
        api_hash=TG_API_HASH,
        in_memory=True
    )
    
    try:
        await client.connect()
        sent_code = await client.send_code(phone_number)
        
        ACTIVE_SESSIONS[phone_number] = client
        
        return AuthResponse(
            status="pending",
            phone_code_hash=sent_code.phone_code_hash,
            message="Code requested successfully."
        )
    except Exception as e:
        logger.error("Failed to request TG code: %s", e)
        try:
            await client.disconnect()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Failed to request code: {str(e)}")


@router.post("/telegram/verify-code", response_model=AuthResponse)
async def verify_tg_code(
    params: CodeVerificationRequest,
    db: Session = Depends(get_db),
):
    """
    Step 2: Submit OTP code to Telegram, export session string, save to DB.
    """
    phone_number = params.phone_number
    
    if phone_number not in ACTIVE_SESSIONS:
        raise HTTPException(status_code=400, detail="No active session found for this phone number. Please request a new code.")
        
    client = ACTIVE_SESSIONS[phone_number]
    
    from pyrogram.errors import SessionPasswordNeeded
    
    try:
        try:
            signed_in = await client.sign_in(
                phone_number,
                params.phone_code_hash,
                params.code
            )
        except SessionPasswordNeeded:
            if not params.password:
                raise HTTPException(status_code=401, detail="SESSION_PASSWORD_NEEDED")
            signed_in = await client.check_password(params.password)
            
        final_session_string = await client.export_session_string()
        
        # Persist to SoulAccount
        account = db.query(SoulAccount).filter(
            SoulAccount.username == phone_number,
            SoulAccount.platform == "telegram"
        ).first()
        
        if not account:
            account = SoulAccount(
                agent_id=params.agent_id,
                platform="telegram",
                username=phone_number,
                password_hash="pyrogram_session",
                device_id=params.device_id,
                auth_cookies={"session_string": final_session_string},
                # An onboarded account bound to an agent must be 'active' so
                # MYRMIDON (which filters on status='active') can use it.
                status="active" if params.agent_id else "unbound",
            )
            db.add(account)
        else:
            account.device_id = params.device_id
            account.auth_cookies = {"session_string": final_session_string}
            if params.agent_id:
                account.agent_id = params.agent_id
                account.status = "active"

        # Activate the linked soul so the binding is immediately usable.
        if params.agent_id:
            profile = (
                db.query(AgentProfile)
                .filter(AgentProfile.agent_id == params.agent_id)
                .first()
            )
            if profile is not None and profile.status != "suspended":
                profile.status = "active"

        db.commit()
        
        return AuthResponse(
            status="success",
            message="Telegram authentication successful."
        )
        
    except Exception as e:
        logger.error("Failed to verify TG code: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to verify code: {str(e)}")
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
        if phone_number in ACTIVE_SESSIONS:
            del ACTIVE_SESSIONS[phone_number]


# ── Stage 23 — Autonomous Session Extraction (replaces manual cookie entry) ──

@router.post("/mobile/extract-session", response_model=AuthResponse)
def extract_mobile_session(
    params: MobileExtractRequest,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("agents:manage")),
) -> AuthResponse:
    """
    Pull a live session (cookies / localStorage / shared_prefs) directly from the
    emulator via MYRMIDON and persist it onto a SoulAccount — no manual JSON.

    If ``agent_id`` is supplied the account is created/updated as bound ('active');
    otherwise it is stored floating ('unbound') for later binding.
    """
    package = params.package or PLATFORM_PACKAGES.get(params.platform.lower())
    target_url = f"{MYRMIDON_DEVICE_URL}/api/v1/devices/{params.device_id}/extract-session"

    try:
        with httpx.Client(timeout=180.0) as client:
            resp = client.get(
                target_url,
                params={"package": package} if package else None,
                headers={"X-Internal-Token": INTERNAL_API_TOKEN},
            )
            resp.raise_for_status()
            session_state = resp.json()
    except httpx.HTTPStatusError as e:
        detail = e.response.text if e.response is not None else str(e)
        raise HTTPException(status_code=502, detail=f"MYRMIDON rejected extraction: {detail}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to reach MYRMIDON Device API: {e}")

    captured = (
        len(session_state.get("cookies", {}))
        + len(session_state.get("local_storage", {}))
        + len(session_state.get("shared_prefs", {}))
    )
    if captured == 0:
        raise HTTPException(
            status_code=422,
            detail=f"No session data captured from {params.device_id}. "
                   f"Ensure the {params.platform} app is logged in and foregrounded. "
                   f"Errors: {session_state.get('errors')}",
        )

    # Upsert the SoulAccount with the extracted session payload.
    account = (
        db.query(SoulAccount)
        .filter(SoulAccount.username == params.username, SoulAccount.platform == params.platform)
        .first()
    )
    bound = bool(params.agent_id)
    if account is None:
        account = SoulAccount(
            agent_id=params.agent_id,
            platform=params.platform,
            username=params.username,
            password_hash="autonomous_session_extract",
            device_id=params.device_id,
            auth_cookies=session_state,
            status="active" if bound else "unbound",
        )
        db.add(account)
    else:
        account.device_id = params.device_id
        account.auth_cookies = session_state
        if bound:
            account.agent_id = params.agent_id
            account.status = "active"
    db.commit()

    return AuthResponse(
        status="success",
        message=(
            f"Extracted session from {params.device_id} "
            f"({len(session_state.get('cookies', {}))} cookies, "
            f"{len(session_state.get('shared_prefs', {}))} pref files) → "
            f"{'bound to ' + params.agent_id if bound else 'stored unbound'}."
        ),
    )
