"""
DAEDALUS — Clone Factory Orchestrator (Stage 24)
==================================================
Autonomous Mass Provisioning. The operator requests N bots of a caste/focus and
the factory runs the whole pipeline per bot, end to end:

    1. Ensure enough AVD emulators exist (boot the deficit via MYRMIDON's
       AVD orchestrator).
    2. Synthesise an `unbound` AgentProfile (soul) via the Genesis engine.
    3. Drive the AVD to auto-register an account (MYRMIDON buys a virtual number,
       types the OTP, extracts the live session).
    4. Persist the SoulAccount and immediately bind it to the soul.

Jobs run as background asyncio tasks; per-bot progress is held in an in-memory
registry that the Clone Factory UI polls. Each bot is independent and fail-soft:
one bot's failure never aborts the batch.
"""

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app.genesis_engine import GenesisSeed, generate_profile
from app.models import AdminUser, AgentProfile, SoulAccount, bind_account_to_soul
from app.rbac import require_permission

logger = logging.getLogger("daedalus.router_factory")

router = APIRouter(prefix="/api/v1/factory", tags=["Clone Factory"])

MYRMIDON_DEVICE_URL = os.getenv("MYRMIDON_DEVICE_URL", "http://myrmidon:8003")
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "morpheus-internal-sync-key")
VALID_CASTES = ("alpha", "beta", "gamma")

# In-memory job registry (single-process uvicorn). job_id → job dict.
JOBS: Dict[str, Dict[str, Any]] = {}


# ── Schemas ────────────────────────────────────────────────────────────────

class MassProvisionRequest(BaseModel):
    count: int = Field(..., ge=1, le=20)
    caste: str = Field("beta")
    target_platform: str = Field("instagram")
    vector_focus: str = Field(..., min_length=1, description="Persona focus/vibe seed")


# ── MYRMIDON helpers ────────────────────────────────────────────────────────

def _myr_headers() -> dict:
    return {"X-Internal-Token": INTERNAL_API_TOKEN}


async def _list_emulators(client: httpx.AsyncClient) -> List[dict]:
    resp = await client.get(f"{MYRMIDON_DEVICE_URL}/api/v1/orchestrator/list", headers=_myr_headers())
    resp.raise_for_status()
    return resp.json().get("emulators", [])


async def _create_emulator(client: httpx.AsyncClient, name: str) -> dict:
    resp = await client.post(
        f"{MYRMIDON_DEVICE_URL}/api/v1/orchestrator/create",
        json={"name": name}, headers=_myr_headers(),
    )
    resp.raise_for_status()
    return resp.json()


def _device_id_of(emu: dict) -> Optional[str]:
    """Derive the ADB device id MYRMIDON drivers expect (localhost:<adb_port>)."""
    port = emu.get("adb_port")
    if port and str(port).isdigit():
        return f"localhost:{port}"
    return None


async def _ensure_emulators(job: Dict[str, Any], count: int) -> List[str]:
    """
    Ensure at least ``count`` emulators exist; boot the deficit. Returns the list
    of device ids to allocate (round-robin). Fail-soft: returns whatever exists.
    """
    device_ids: List[str] = []
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            existing = await _list_emulators(client)
            running = [e for e in existing if "running" in str(e.get("status", "")).lower() or "up" in str(e.get("status", "")).lower()]
            for e in running:
                did = _device_id_of(e)
                if did:
                    device_ids.append(did)

            deficit = count - len(device_ids)
            if deficit > 0:
                job["log"].append(f"Booting {deficit} new AVD(s) (have {len(device_ids)}, need {count}).")
            for i in range(deficit):
                name = f"clone-avd-{uuid.uuid4().hex[:6]}"
                res = await _create_emulator(client, name)
                did = _device_id_of(res)
                if did:
                    device_ids.append(did)
                    job["log"].append(f"Booted AVD {name} → {did}.")
                else:
                    job["log"].append(f"AVD {name} create returned: {res}")
    except Exception as e:
        job["log"].append(f"Emulator provisioning degraded: {e}")
        logger.warning("Clone Factory emulator provisioning failed: %s", e)

    return device_ids


async def _auto_register(client: httpx.AsyncClient, device_id: str, platform: str, agent_id: str, full_name: str) -> dict:
    resp = await client.post(
        f"{MYRMIDON_DEVICE_URL}/api/v1/devices/{device_id}/auto-register",
        json={"platform": platform, "agent_id": agent_id, "full_name": full_name},
        headers=_myr_headers(),
    )
    resp.raise_for_status()
    return resp.json()


# ── Per-bot pipeline ─────────────────────────────────────────────────────────

def _set(bot: Dict[str, Any], stage: str, **extra) -> None:
    bot["stage"] = stage
    bot.update(extra)
    logger.info("Clone Factory bot #%s → %s", bot["index"], stage)


async def _provision_bot(bot: Dict[str, Any], req: MassProvisionRequest, device_id: Optional[str]) -> None:
    """Run the full pipeline for a single bot, updating its live status in place."""
    db: Session = SessionLocal()
    try:
        bot["device_id"] = device_id

        # 1. Synthesise the soul (Genesis) — blocking LLM call off the loop.
        _set(bot, "generating_persona")
        agent_id = f"clone_{req.caste}_{uuid.uuid4().hex[:8]}"
        bot["agent_id"] = agent_id
        seed = GenesisSeed(
            caste=req.caste,
            agent_id=agent_id,
            codename=f"Clone-{bot['index']}-{agent_id[-4:]}",
            focus=req.vector_focus,
            platforms=[req.target_platform],
        )
        profile_data = await asyncio.to_thread(generate_profile, seed)
        profile = AgentProfile(**profile_data)
        db.add(profile)
        db.commit()

        if not device_id:
            _set(bot, "failed", error="No emulator available to register on.", status="failed")
            return

        # 2. Drive the AVD to register + extract the session.
        _set(bot, "registering")
        async with httpx.AsyncClient(timeout=300.0) as client:
            reg = await _auto_register(client, device_id, req.target_platform, agent_id, profile_data.get("full_name") or agent_id)

        if not reg.get("success"):
            _set(bot, "failed", error=reg.get("error", "registration failed"), status="failed", phone=reg.get("phone"))
            return

        # 3. Persist the SoulAccount and bind it to the soul.
        _set(bot, "binding", phone=reg.get("phone"))
        account = SoulAccount(
            agent_id=None,
            platform=req.target_platform,
            username=reg.get("phone") or agent_id,
            password_hash="autonomous_registration",
            device_id=device_id,
            auth_cookies=reg.get("session"),
            status="unbound",
        )
        db.add(account)
        db.commit()
        db.refresh(account)

        bound = bind_account_to_soul(db, account.id, agent_id)
        _set(bot, "bound", status="done", account_id=bound.id)
    except Exception as e:
        logger.exception("Clone Factory bot #%s crashed.", bot["index"])
        _set(bot, "failed", error=str(e), status="failed")
        db.rollback()
    finally:
        db.close()


async def _run_job(job_id: str, req: MassProvisionRequest) -> None:
    """Background batch runner: provision emulators, then each bot sequentially."""
    job = JOBS[job_id]
    job["status"] = "provisioning_emulators"
    device_ids = await _ensure_emulators(job, req.count)

    job["status"] = "running"
    # Bots run sequentially: one shared Appium server + anti-fraud pacing.
    for i, bot in enumerate(job["bots"]):
        device_id = device_ids[i % len(device_ids)] if device_ids else None
        await _provision_bot(bot, req, device_id)

    done = sum(1 for b in job["bots"] if b.get("status") == "done")
    job["status"] = "completed"
    job["summary"] = {"bound": done, "failed": len(job["bots"]) - done, "total": len(job["bots"])}
    job["finished_at"] = datetime.now(timezone.utc).isoformat()


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/mass-provision")
async def mass_provision(
    request: MassProvisionRequest,
    _user: AdminUser = Depends(require_permission("agents:manage")),
) -> Dict[str, Any]:
    """Kick off an autonomous mass-provisioning batch and return the job handle."""
    if request.caste.lower() not in VALID_CASTES:
        raise HTTPException(status_code=400, detail=f"Invalid caste. Allowed: {list(VALID_CASTES)}")

    job_id = uuid.uuid4().hex
    job = {
        "job_id": job_id,
        "status": "queued",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "params": request.model_dump(),
        "log": [],
        "bots": [
            {"index": i + 1, "stage": "queued", "status": "pending",
             "agent_id": None, "device_id": None, "phone": None,
             "account_id": None, "error": None}
            for i in range(request.count)
        ],
    }
    JOBS[job_id] = job

    # Launch the batch without blocking the request.
    asyncio.create_task(_run_job(job_id, request))

    return {"job_id": job_id, "status": job["status"], "bots": job["bots"]}


@router.get("/jobs/{job_id}")
def get_job(
    job_id: str,
    _user: AdminUser = Depends(require_permission("agents:view")),
) -> Dict[str, Any]:
    """Live status of a provisioning job (polled by the UI execution monitor)."""
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/jobs")
def list_jobs(
    _user: AdminUser = Depends(require_permission("agents:view")),
) -> Dict[str, Any]:
    """Recent provisioning jobs, newest first."""
    jobs = sorted(JOBS.values(), key=lambda j: j["created_at"], reverse=True)
    return {"jobs": jobs[:25], "total": len(JOBS)}
