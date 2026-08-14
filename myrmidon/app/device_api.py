"""
MYRMIDON — Device Status API
================================
FastAPI mini-router exposing ADB device telemetry
for the DAEDALUS admin dashboard Device Map Grid.

Mounted in myrmidon's main.py as a lightweight HTTP server
running alongside the Redis consumer loop.
"""

import logging
import os
import threading
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, Header
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
import uvicorn

from app.adb_supervisor import ADBSupervisor

logger = logging.getLogger("myrmidon.device_api")

INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "morpheus-internal-sync-key")
DEVICE_API_PORT = int(os.getenv("DEVICE_API_PORT", "8003"))

app = FastAPI(title="MYRMIDON Device API", version="0.1.0")

# Lazy-initialized supervisor (created on first request)
_supervisor = None


def _get_supervisor() -> ADBSupervisor:
    global _supervisor
    if _supervisor is None:
        _supervisor = ADBSupervisor()
    return _supervisor


def _verify_token(token: str) -> None:
    if token != INTERNAL_API_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid internal token")


@app.get("/api/v1/ping")
def ping(x_internal_token: str = Header(None, alias="X-Internal-Token")) -> Dict[str, str]:
    """Lightweight ping for latency measurement."""
    _verify_token(x_internal_token)
    return {"status": "ok"}


_session_factory = None


def _get_session_factory():
    global _session_factory
    if _session_factory is None:
        from app.main import connect_postgres
        _session_factory = connect_postgres()
    return _session_factory


@app.get("/api/v1/telegram/{agent_id}/channels")
def telegram_channels(
    agent_id: str,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
) -> Dict[str, Any]:
    """
    Live-enumerate the channels/groups the agent's Telegram account is subscribed
    to (its universe of targets / news sources). Opens the Pyrogram session under
    the per-agent lock; sync endpoint → FastAPI runs it in a threadpool.
    """
    _verify_token(x_internal_token)
    from app.main import get_agent_credentials
    from app.drivers.tg_client import TelegramDriver

    creds = get_agent_credentials(_get_session_factory(), agent_id, "telegram")
    if creds is None:
        raise HTTPException(status_code=404, detail="No active Telegram account for this agent.")
    driver = TelegramDriver(agent_id, creds)
    channels = driver.list_channels()
    return {"agent_id": agent_id, "channels": channels, "total": len(channels)}


@app.get("/api/v1/telegram/{agent_id}/export")
def telegram_export(
    agent_id: str,
    channel: str,
    post_limit: int = 10,
    comment_limit: int = 40,
    post_id: int = 0,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
) -> Dict[str, Any]:
    """
    Export a channel's recent posts WITH the real comments under them.

    Consumed by the SIMULATION polygon's import: a polygon thread populated only by
    the operator and by our own agents cannot exercise the part of the pipeline that
    reads a real crowd's mood. Comments live in the linked discussion group and need
    MTProto, which only MYRMIDON holds.

    Strictly read-only — nothing is posted, joined or reacted to.
    """
    _verify_token(x_internal_token)
    from app.main import get_agent_credentials
    from app.drivers.tg_client import TelegramDriver

    creds = get_agent_credentials(_get_session_factory(), agent_id, "telegram")
    if creds is None:
        raise HTTPException(status_code=404, detail="No active Telegram account for this agent.")
    driver = TelegramDriver(agent_id, creds)
    data = driver.export_thread(channel, post_limit=post_limit,
                                comment_limit=comment_limit, post_id=post_id or None)
    if data.get("error"):
        raise HTTPException(status_code=502, detail=f"Export failed: {data['error']}")
    return {"agent_id": agent_id, **data}


@app.get("/api/v1/devices")
def list_devices(
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
) -> Dict[str, Any]:
    """
    List all connected ADB devices with basic status info.
    Used by DAEDALUS Device Map Grid.
    """
    _verify_token(x_internal_token)
    supervisor = _get_supervisor()
    devices = supervisor.list_connected_devices()
    return {"devices": devices, "total": len(devices)}


@app.get("/api/v1/devices/{device_id}/info")
def device_info(
    device_id: str,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
) -> Dict[str, Any]:
    """
    Detailed telemetry for a specific device:
    battery, CPU load, memory, proxy status, screen state.
    """
    _verify_token(x_internal_token)
    supervisor = _get_supervisor()
    info = supervisor.get_device_info(device_id)
    if info.get("state") == "error":
        raise HTTPException(status_code=404, detail=info.get("error", "Device not found"))
    return info


@app.post("/api/v1/devices/{device_id}/proxy")
def set_device_proxy(
    device_id: str,
    proxy_host: str,
    proxy_port: int,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
) -> Dict[str, str]:
    """Set OS-level proxy on a device."""
    _verify_token(x_internal_token)
    supervisor = _get_supervisor()
    success = supervisor.enforce_os_level_proxy(device_id, proxy_host, proxy_port)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to set proxy")
    return {"status": "success", "proxy": f"{proxy_host}:{proxy_port}"}


@app.delete("/api/v1/devices/{device_id}/proxy")
def clear_device_proxy(
    device_id: str,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
) -> Dict[str, str]:
    """Clear OS-level proxy on a device."""
    _verify_token(x_internal_token)
    supervisor = _get_supervisor()
    success = supervisor.clear_os_proxy(device_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to clear proxy")
    return {"status": "success"}


@app.post("/api/v1/devices/{device_id}/launch")
def launch_app(
    device_id: str,
    package: str,
    activity: str,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
) -> Dict[str, str]:
    """Launch an app on a device."""
    _verify_token(x_internal_token)
    supervisor = _get_supervisor()
    success = supervisor.launch_app_activity(device_id, package, activity)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to launch app")
    return {"status": "success"}


@app.post("/api/v1/devices/{device_id}/stop")
def stop_app(
    device_id: str,
    package: str,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
) -> Dict[str, str]:
    """Force-stop an app on a device."""
    _verify_token(x_internal_token)
    supervisor = _get_supervisor()
    success = supervisor.force_stop_app(device_id, package)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to stop app")
    return {"status": "success"}


from pydantic import BaseModel

class SnapshotRequest(BaseModel):
    snapshot_name: str

@app.post("/api/v1/devices/{device_id}/snapshot/load")
def snapshot_load(
    device_id: str,
    body: SnapshotRequest,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
) -> Dict[str, str]:
    """Load an emulator snapshot."""
    _verify_token(x_internal_token)
    supervisor = _get_supervisor()
    success = supervisor.manage_device_snapshot(device_id, "load", body.snapshot_name)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to load snapshot")
    return {"status": "success"}


@app.post("/api/v1/devices/{device_id}/snapshot/save")
def snapshot_save(
    device_id: str,
    body: SnapshotRequest,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
) -> Dict[str, str]:
    """Save an emulator snapshot."""
    _verify_token(x_internal_token)
    supervisor = _get_supervisor()
    success = supervisor.manage_device_snapshot(device_id, "save", body.snapshot_name)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save snapshot")
    return {"status": "success"}


class ClearCacheRequest(BaseModel):
    package: str

@app.post("/api/v1/devices/{device_id}/clear-cache")
def clear_cache(
    device_id: str,
    body: ClearCacheRequest,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
) -> Dict[str, str]:
    """Purge application cache."""
    _verify_token(x_internal_token)
    supervisor = _get_supervisor()
    success = supervisor.purge_application_cache(device_id, body.package)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to clear app cache")
    return {"status": "success"}


@app.post("/api/v1/devices/{device_id}/reboot")
def hard_reboot(
    device_id: str,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
) -> Dict[str, str]:
    """Trigger a cold reboot of the emulator."""
    _verify_token(x_internal_token)
    supervisor = _get_supervisor()
    success = supervisor.hard_reboot_emulator(device_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to hard reboot emulator")
    return {"status": "success"}


# ── Sandbox Direct Execution Engine (Stage 16) ──────────────────────────────

class SandboxExecuteRequest(BaseModel):
    """Direct, queue-bypassing typing run targeting a single AVD."""
    agent_id: str = Field(..., description="Persona instantiating the driver")
    target_app: str = Field("base", description="instagram | telegram | base")
    text_payload: str = Field(..., min_length=1, description="Text to physically type")


def _run_sandbox_typing(device_id: str, body: "SandboxExecuteRequest") -> Dict[str, Any]:
    """
    Blocking worker: builds the correct driver and performs the W3C
    coordinate typing sequence. Executed inside a threadpool so the FastAPI
    event loop is never blocked by the long-running Appium session.
    """
    target = (body.target_app or "base").strip().lower()
    credentials: Dict[str, Any] = {}

    if target in ("instagram", "ig"):
        from app.drivers.instagram import InstagramDriver
        driver = InstagramDriver(body.agent_id, credentials, device_id=device_id)
    else:
        # Generic base driver — opens whatever is foregrounded and types into it.
        from app.drivers.mobile_base import BaseMobileDriver
        driver = BaseMobileDriver(body.agent_id, credentials, device_id=device_id)

    return driver.sandbox_type(body.text_payload)


@app.post("/api/v1/devices/{device_id}/sandbox-execute")
async def sandbox_execute(
    device_id: str,
    body: SandboxExecuteRequest,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
) -> Dict[str, Any]:
    """
    Trigger an immediate, isolated typing sequence on a specific AVD.

    Bypasses the Redis event queues entirely. Instantiates the appropriate
    mobile driver, focuses an input element, and executes the hardened
    coordinate-based `human_type()` engine with the supplied payload.
    Returns a definitive success/failure log without crashing the API thread.
    """
    _verify_token(x_internal_token)
    logger.info(
        "Sandbox-execute on %s: agent=%s app=%s (%d chars)",
        device_id, body.agent_id, body.target_app, len(body.text_payload),
    )
    try:
        result = await run_in_threadpool(_run_sandbox_typing, device_id, body)
    except Exception as e:
        logger.error("Sandbox-execute crashed for %s: %s", device_id, e)
        return {
            "status": "error",
            "success": False,
            "device_id": device_id,
            "log": [f"[FAIL] Driver dispatch crashed: {e}"],
        }

    result["device_id"] = device_id
    result["agent_id"] = body.agent_id
    return result


# ── Autonomous Registration (Stage 24 — Clone Factory) ──────────────────────

class AutoRegisterRequest(BaseModel):
    platform: str = Field("instagram", description="instagram | telegram | twitter | threads | youtube")
    agent_id: str = Field(..., description="Soul this account will be bound to")
    full_name: str | None = Field(None, description="Display name to register with")
    service: str | None = Field(None, description="Override SMS service code")


@app.post("/api/v1/devices/{device_id}/auto-register")
async def auto_register(
    device_id: str,
    body: AutoRegisterRequest,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
) -> Dict[str, Any]:
    """
    Autonomously register a brand-new account on ``device_id``: buy a virtual
    number, drive the app via coordinate typing, intercept the OTP, set up the
    profile, and extract the live session. Returns the session payload + phone.
    """
    _verify_token(x_internal_token)
    from app.drivers.registration_driver import run_auto_registration

    logger.info("auto-register on %s: platform=%s agent=%s", device_id, body.platform, body.agent_id)
    result = await run_auto_registration(
        device_id=device_id,
        platform=body.platform,
        agent_id=body.agent_id,
        full_name=body.full_name,
        service=body.service,
    )
    result["device_id"] = device_id
    return result


# ── Autonomous Session Extraction (Stage 23) ────────────────────────────────

def _run_extract_session(device_id: str, package: str | None) -> Dict[str, Any]:
    """
    Blocking worker (threadpool): dump the live session of the foregrounded app.

    Combines two real sources:
      • Appium WEBVIEW cookies + localStorage (web/hybrid apps).
      • Rooted ADB ``shared_prefs`` XML dump (native apps).
    """
    from app.drivers.mobile_base import BaseMobileDriver

    payload: Dict[str, Any] = {
        "type": "unknown", "package": package,
        "cookies": {}, "local_storage": {}, "shared_prefs": {}, "errors": [],
    }

    # Appium-driven webview/hybrid + (where the server permits) shared_prefs.
    driver = BaseMobileDriver("session-extractor", {}, device_id=device_id)
    try:
        payload = driver.extract_session_state(package_name=package)
    except Exception as e:
        payload["errors"].append(f"appium_extract: {e}")
    finally:
        driver.close_session()

    # Authoritative native fallback over rooted ADB (does not need Appium).
    if package and not payload.get("shared_prefs"):
        try:
            prefs = _get_supervisor().dump_shared_prefs(device_id, package)
            if prefs:
                payload["shared_prefs"] = prefs
                payload["type"] = "hybrid" if payload.get("type") == "web" else "native"
        except Exception as e:
            payload.setdefault("errors", []).append(f"adb_shared_prefs: {e}")

    return payload


@app.get("/api/v1/devices/{device_id}/extract-session")
async def extract_session(
    device_id: str,
    package: str | None = None,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
) -> Dict[str, Any]:
    """
    Autonomously extract the live session (cookies / localStorage / shared_prefs)
    from the app running on a device — eliminating manual cookie entry. DAEDALUS
    pulls this and persists it onto the SoulAccount.
    """
    _verify_token(x_internal_token)
    logger.info("extract-session on %s (package=%s)", device_id, package)
    try:
        result = await run_in_threadpool(_run_extract_session, device_id, package)
    except Exception as e:
        logger.error("extract-session crashed for %s: %s", device_id, e)
        raise HTTPException(status_code=502, detail=f"Session extraction failed: {e}")
    result["device_id"] = device_id
    return result


from app.avd_orchestrator import get_orchestrator

@app.get("/api/v1/orchestrator/list")
def list_orchestrated_emulators(
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
) -> Dict[str, Any]:
    _verify_token(x_internal_token)
    orch = get_orchestrator()
    emulators = orch.list_emulators()
    return {"emulators": emulators}

class ProvisionRequest(BaseModel):
    name: str

@app.post("/api/v1/orchestrator/create")
def create_orchestrated_emulator(
    body: ProvisionRequest,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
) -> Dict[str, Any]:
    _verify_token(x_internal_token)
    orch = get_orchestrator()
    res = orch.create_emulator(body.name)
    if "error" in res:
        raise HTTPException(status_code=500, detail=res["error"])
    return res

@app.delete("/api/v1/orchestrator/{name}")
def delete_orchestrated_emulator(
    name: str,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
) -> Dict[str, str]:
    _verify_token(x_internal_token)
    orch = get_orchestrator()
    if not orch.delete_emulator(name):
        raise HTTPException(status_code=500, detail="Failed to delete emulator")
    return {"status": "success"}

@app.post("/api/v1/orchestrator/{name}/stop")
def stop_orchestrated_emulator(
    name: str,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
) -> Dict[str, str]:
    _verify_token(x_internal_token)
    orch = get_orchestrator()
    if not orch.stop_emulator(name):
        raise HTTPException(status_code=500, detail="Failed to stop emulator")
    return {"status": "success"}


def start_device_api_server() -> None:
    """
    Start the device API as a background thread.
    Called from myrmidon's main entrypoint.
    """
    def _run():
        logger.info("Starting Device API server on port %d...", DEVICE_API_PORT)
        uvicorn.run(app, host="0.0.0.0", port=DEVICE_API_PORT, log_level="warning")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    logger.info("Device API server thread started on port %d.", DEVICE_API_PORT)
