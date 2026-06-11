import logging
import docker
from typing import Dict, Any, List

logger = logging.getLogger("myrmidon.orchestrator")

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

# Global singleton instance
_orchestrator = None

def get_orchestrator() -> AVDOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = AVDOrchestrator()
    return _orchestrator
