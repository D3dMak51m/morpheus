"""
DAEDALUS — Auth Factory
=======================
Interactive state machine for platform onboarding.
Supports Pyrogram OTP handshake for Telegram and generic session import for mobile apps.
"""

import logging
import os
from typing import Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from sqlalchemy.orm import Session
from pyrogram import Client

from app.database import get_db
from app.models import SoulAccount
from app.rbac import require_permission, AdminUser

logger = logging.getLogger("daedalus.auth_factory")

router = APIRouter(prefix="/api/v1/auth-factory", tags=["Auth Factory"])

TG_API_ID = os.getenv("TG_API_ID", "2040")
TG_API_HASH = os.getenv("TG_API_HASH", "b18441a1ff607e10a989891a5462e627")
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "morpheus-internal-sync-key")

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
                auth_cookies={"session_string": final_session_string}
            )
            db.add(account)
        else:
            account.device_id = params.device_id
            account.auth_cookies = {"session_string": final_session_string}
            
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
