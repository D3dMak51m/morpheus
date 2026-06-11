import asyncio
import logging
import os
import socket
import threading
import time
from typing import Dict, Any, List

import docker
import httpx

logger = logging.getLogger("myrmidon.orchestrator")

# ── Self-healing configuration ─────────────────────────────────────────────
HEALTH_CHECK_INTERVAL_SEC = int(os.getenv("AVD_HEALTH_INTERVAL_SEC", "30"))
UNHEALTHY_THRESHOLD_SEC = int(os.getenv("AVD_UNHEALTHY_THRESHOLD_SEC", "180"))  # 3 minutes
DOCKER_HOST_GATEWAY = os.getenv("DOCKER_HOST_GATEWAY", "host.docker.internal")
DAEDALUS_URL = os.getenv("DAEDALUS_URL", "http://daedalus:8000")
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "morpheus-internal-sync-key")

class AVDOrchestrator:
    def __init__(self):
        try:
            self.client = docker.from_env()
            logger.info("AVDOrchestrator initialized. Connected to Docker daemon.")
        except Exception as e:
            logger.error("AVDOrchestrator failed to connect to Docker daemon: %s", e)
            self.client = None

    def list_emulators(self) -> List[Dict[str, Any]]:
        if not self.client:
            return []
        
        try:
            containers = self.client.containers.list(all=True, filters={"label": "morpheus_emulator=true"})
            results = []
            for c in containers:
                ports = c.attrs.get('NetworkSettings', {}).get('Ports', {})
                
                vnc_port = 'unknown'
                adb_port = 'unknown'
                
                if ports:
                    vnc_bindings = ports.get('6080/tcp')
                    adb_bindings = ports.get('5555/tcp')
                    if vnc_bindings:
                        vnc_port = vnc_bindings[0].get('HostPort', 'unknown')
                    if adb_bindings:
                        adb_port = adb_bindings[0].get('HostPort', 'unknown')
                
                results.append({
                    "id": c.short_id,
                    "name": c.name,
                    "status": c.status,
                    "vnc_port": vnc_port,
                    "adb_port": adb_port
                })
            return results
        except Exception as e:
            logger.error("Failed to list emulators: %s", e)
            return []

    def create_emulator(self, name: str) -> Dict[str, Any]:
        if not self.client:
            return {"error": "Docker not connected"}
        
        try:
            existing = self.list_emulators()
            # Simple port allocation logic
            base_vnc = 6080
            base_adb = 5555
            
            used_vnc = [int(e['vnc_port']) for e in existing if str(e['vnc_port']).isdigit()]
            used_adb = [int(e['adb_port']) for e in existing if str(e['adb_port']).isdigit()]
            
            vnc_port = max(used_vnc) + 1 if used_vnc else base_vnc
            adb_port = max(used_adb) + 2 if used_adb else base_adb
            
            logger.info("Provisioning budtmo/docker-android container: %s", name)
            
            container = self.client.containers.run(
                "budtmo/docker-android:emulator_11.0",
                name=name,
                detach=True,
                privileged=True,
                environment={
                    "DEVICE": "Nexus 5",
                    "WEB_VNC": "true",
                    "EMULATOR_ARGS": "-gpu swiftshader_indirect -no-audio"
                },
                ports={
                    "6080/tcp": vnc_port,
                    "5554/tcp": adb_port - 1,
                    "5555/tcp": adb_port
                },
                labels={"morpheus_emulator": "true"}
                # Intentionally omitting /dev/kvm mount to avoid strict failure if host KVM is missing.
                # budtmo emulator falls back to software rendering (slow, but works) if KVM is absent.
            )
            
            logger.info("Successfully provisioned %s (VNC: %s, ADB: %s)", name, vnc_port, adb_port)
            return {
                "status": "success", 
                "name": name, 
                "vnc_port": vnc_port, 
                "adb_port": adb_port
            }
        except Exception as e:
            logger.error("Failed to provision emulator %s: %s", name, e)
            return {"error": str(e)}

    def stop_emulator(self, name: str) -> bool:
        if not self.client:
            return False
        try:
            c = self.client.containers.get(name)
            c.stop(timeout=5)
            return True
        except Exception as e:
            logger.error("Failed to stop emulator %s: %s", name, e)
            return False

    def delete_emulator(self, name: str) -> bool:
        if not self.client:
            return False
        try:
            c = self.client.containers.get(name)
            c.remove(force=True)
            return True
        except Exception as e:
            logger.error("Failed to delete emulator %s: %s", name, e)
            return False

    # ── Self-healing health probes (Stage 19) ─────────────────────────────

    def _adb_socket_alive(self, adb_port: Any, timeout: float = 3.0) -> bool:
        """
        Liveness probe: TCP-connect to the emulator's published ADB port via the
        Docker host gateway. A frozen/crashed Android drops this socket even if
        the container's Docker status still reports 'running'.
        """
        try:
            port = int(adb_port)
        except (TypeError, ValueError):
            return False
        try:
            with socket.create_connection((DOCKER_HOST_GATEWAY, port), timeout=timeout):
                return True
        except OSError:
            return False

    def check_emulator_health(self) -> List[Dict[str, Any]]:
        """List emulators annotated with a `healthy` flag (Docker status + ADB probe)."""
        results: List[Dict[str, Any]] = []
        for e in self.list_emulators():
            status = str(e.get("status", "")).lower()
            running = "running" in status or "up" in status
            e["healthy"] = bool(running and self._adb_socket_alive(e.get("adb_port")))
            results.append(e)
        return results

    def heal_emulator(self, emu: Dict[str, Any]) -> Dict[str, Any]:
        """
        Forcefully destroy a frozen emulator and provision a fresh replacement
        with the same name/parameters. Returns the provisioning result.
        """
        name = emu.get("name")
        logger.error("SELF-HEAL: emulator '%s' unresponsive — destroying & reprovisioning.", name)
        self.delete_emulator(name)
        time.sleep(2)
        result = self.create_emulator(name)
        if "error" in result:
            logger.error("SELF-HEAL: failed to reprovision '%s': %s", name, result["error"])
        else:
            logger.info("SELF-HEAL: '%s' reprovisioned (ADB %s, VNC %s).",
                        name, result.get("adb_port"), result.get("vnc_port"))
        return result


# ── Notification + self-healing daemon (Stage 19) ──────────────────────────

def _notify_device_status(device_id: str, status: str) -> None:
    """Inform DAEDALUS of a VirtualDevice lifecycle transition (best-effort)."""
    try:
        httpx.post(
            f"{DAEDALUS_URL}/api/v1/souls/internal/device-status",
            json={"device_id": device_id, "status": status},
            headers={"X-Internal-Token": INTERNAL_API_TOKEN},
            timeout=10.0,
        )
        logger.info("Device %s → %s reported to DAEDALUS.", device_id, status)
    except Exception as exc:
        logger.error("Failed to report device status to DAEDALUS: %s", exc)


def _heal_and_notify(orch: "AVDOrchestrator", emu: Dict[str, Any]) -> None:
    """Blocking heal routine: RECOVERING → destroy → reprovision → ONLINE."""
    old_device_id = f"localhost:{emu.get('adb_port')}"
    _notify_device_status(old_device_id, "RECOVERING")

    result = orch.heal_emulator(emu)
    if "error" in result:
        _notify_device_status(old_device_id, "OFFLINE")
        return

    new_device_id = f"localhost:{result.get('adb_port')}"
    _notify_device_status(new_device_id, "ONLINE")


# Per-container timestamp of when it was first observed unhealthy.
_unhealthy_since: Dict[str, float] = {}


async def health_monitor_loop() -> None:
    """
    Constantly running self-healing monitor. Pings every active emulator; any
    container unresponsive for longer than UNHEALTHY_THRESHOLD_SEC (3 min) is
    forcefully recovered. Runs in a dedicated thread's event loop so it never
    blocks MYRMIDON's task consumer or the Device API server.
    """
    orch = get_orchestrator()
    logger.info(
        "AVD self-healing monitor started (interval=%ds, threshold=%ds).",
        HEALTH_CHECK_INTERVAL_SEC, UNHEALTHY_THRESHOLD_SEC,
    )
    while True:
        try:
            emulators = await asyncio.to_thread(orch.check_emulator_health)
            now = time.time()
            seen = set()

            for emu in emulators:
                name = emu.get("name")
                seen.add(name)
                if emu.get("healthy"):
                    _unhealthy_since.pop(name, None)
                    continue

                first_seen = _unhealthy_since.setdefault(name, now)
                elapsed = now - first_seen
                logger.warning("Emulator '%s' unhealthy for %.0fs.", name, elapsed)
                if elapsed >= UNHEALTHY_THRESHOLD_SEC:
                    await asyncio.to_thread(_heal_and_notify, orch, emu)
                    _unhealthy_since.pop(name, None)

            # Forget tracking for containers that no longer exist.
            for stale in [n for n in _unhealthy_since if n not in seen]:
                _unhealthy_since.pop(stale, None)

        except Exception:
            logger.exception("Health monitor tick failed.")

        await asyncio.sleep(HEALTH_CHECK_INTERVAL_SEC)


def start_health_monitor() -> None:
    """Launch the self-healing monitor in a daemon thread with its own loop."""
    def _run() -> None:
        try:
            asyncio.run(health_monitor_loop())
        except Exception:
            logger.exception("Health monitor thread crashed.")

    thread = threading.Thread(target=_run, name="avd-health-monitor", daemon=True)
    thread.start()
    logger.info("AVD self-healing monitor thread launched.")


# Global singleton instance
_orchestrator = None

def get_orchestrator() -> AVDOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = AVDOrchestrator()
    return _orchestrator
