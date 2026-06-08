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
