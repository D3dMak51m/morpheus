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
