"""
MYRMIDON — ADB Supervisor (Stage 8)
======================================
Production-grade ADB lifecycle controller using pure-python-adb (ppadb).
Manages physical/virtual Android devices mapped to agent personas via PostgreSQL.
Features a ThreadPoolExecutor background monitoring loop and an automated
Appium recovery machine.
"""

import logging
import os
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

from ppadb.client import Client as AdbClient
from sqlalchemy import create_engine, text

logger = logging.getLogger("myrmidon.adb_supervisor")

ADB_HOST = os.getenv("ADB_HOST", "host.docker.internal")
ADB_PORT = int(os.getenv("ADB_PORT", "5037"))

DB_USER = os.getenv("DB_USER", "morpheus_admin")
DB_PASSWORD = os.getenv("DB_PASSWORD", "morpheus_secure_pass")
DB_HOST_ENV = os.getenv("DB_HOST", "postgres")
DB_PORT_ENV = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "morpheus_db")


class ADBSupervisor:
    """
    Controls physical/virtual Android devices via ADB protocol.
    Maintains a thread pool for background monitoring and automated
    recovery routines.
    """

    def __init__(
        self,
        host: str = ADB_HOST,
        port: int = ADB_PORT,
        max_workers: int = 5,
        monitor_interval: int = 30
    ) -> None:
        self._host: str = host
        self._port: int = port
        self._client: Optional[AdbClient] = None
        
        self.max_workers: int = max_workers
        self.monitor_interval: int = monitor_interval
        self._executor: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=self.max_workers)
        self._stop_event: threading.Event = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None

        self._agent_to_device_map: Dict[str, str] = {}

        self._connect()
        self._init_db()

    def _connect(self) -> None:
        """Establish connection to the ADB server."""
        try:
            self._client = AdbClient(host=self._host, port=self._port)
            logger.info(
                "ADBSupervisor: Connected to ADB server at %s:%d",
                self._host, self._port,
            )
        except Exception as e:
            logger.error(
                "ADBSupervisor: Failed to connect to ADB server at %s:%d — %s",
                self._host, self._port, e,
            )
            self._client = None

    def _init_db(self) -> None:
        """Initialize PostgreSQL engine for dynamic device mapping."""
        db_url = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST_ENV}:{DB_PORT_ENV}/{DB_NAME}"
        try:
            self._db_engine = create_engine(
                db_url,
                pool_size=5,
                max_overflow=10,
                pool_pre_ping=True,
            )
            logger.info("ADBSupervisor: DB connection established for dynamic mapping.")
        except Exception as e:
            logger.error("ADBSupervisor: DB connection failed — %s", e)
            self._db_engine = None

    def start_monitoring(self) -> None:
        """Starts the background monitoring loop."""
        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            logger.warning("ADBSupervisor: Monitoring is already running.")
            return

        self._stop_event.clear()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
        logger.info("ADBSupervisor: Proactive background monitoring loop started.")

    def stop_monitoring(self) -> None:
        """Stops the background monitoring loop."""
        self._stop_event.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5.0)
        self._executor.shutdown(wait=False)
        logger.info("ADBSupervisor: Proactive background monitoring loop stopped.")

    # ── Snapshot Management ────────────────────────────────────────────────

    def manage_device_snapshot(self, device_id: str, action: str, snapshot_name: str = "idle_snap") -> bool:
        """
        Manages emulator snapshots to ensure isolated execution states.
        action can be 'load' or 'save'.
        """
        if not device_id.startswith("emulator-"):
            logger.warning("ADBSupervisor: Snapshot management is only supported on emulators. Skipping for %s", device_id)
            return False
            
        try:
            device = self._get_device(device_id)
            if action == 'save':
                logger.info("ADBSupervisor [%s]: Saving state to snapshot '%s'", device_id, snapshot_name)
                # Ensure the emulator accepts the console command
                output = device.shell(f"emu avd snapshot save {snapshot_name}")
                return "OK" in output or "Error" not in output
            elif action == 'load':
                logger.info("ADBSupervisor [%s]: Loading state from snapshot '%s'", device_id, snapshot_name)
                output = device.shell(f"emu avd snapshot load {snapshot_name}")
                return "OK" in output or "Error" not in output
            else:
                logger.error("ADBSupervisor [%s]: Unknown snapshot action '%s'", device_id, action)
                return False
        except Exception as e:
            logger.error("ADBSupervisor [%s]: Snapshot %s failed — %s", device_id, action, e)
            return False

    def shutdown_device(self, device_id: str) -> bool:
        """Shuts down the emulator to reclaim host resources."""
        if not device_id.startswith("emulator-"):
            logger.warning("ADBSupervisor: Shutdown is only supported on emulators. Skipping for %s", device_id)
            return False
            
        try:
            logger.info("ADBSupervisor [%s]: Initiating asynchronous shutdown to reclaim resources.", device_id)
            device = self._get_device(device_id)
            device.shell("reboot -p") # or "emu kill" depending on the emulator setup
            return True
        except Exception as e:
            logger.error("ADBSupervisor [%s]: Failed to shutdown device — %s", device_id, e)
            return False

    def _monitor_loop(self) -> None:
        """
        Periodically polls the database for active mappings and checks device statuses 
        concurrently via the ThreadPoolExecutor.
        """
        while not self._stop_event.is_set():
            try:
                self._sync_device_mappings()
                
                # Check status for all mapped devices concurrently
                futures = []
                for agent_id, device_id in self._agent_to_device_map.items():
                    futures.append(
                        self._executor.submit(self._check_and_log_status, agent_id, device_id)
                    )
                
                # Wait for all checks to complete this cycle
                for f in futures:
                    f.result(timeout=10.0)

            except Exception as e:
                logger.error("ADBSupervisor: Error in monitor loop — %s", e)
            
            self._stop_event.wait(self.monitor_interval)

    def _sync_device_mappings(self) -> None:
        """Pulls dynamic agent_id -> device_id mapping from PostgreSQL."""
        if not self._db_engine:
            return

        try:
            with self._db_engine.connect() as conn:
                # Assuming 'agent_id' and 'device_id' exist in souls_accounts
                result = conn.execute(text("SELECT agent_id, device_id FROM souls_accounts WHERE status = 'active' AND device_id IS NOT NULL"))
                new_map = {row[0]: row[1] for row in result}
                self._agent_to_device_map = new_map
        except Exception as e:
            logger.error("ADBSupervisor: Failed to sync device mappings — %s", e)

    def _check_and_log_status(self, agent_id: str, device_id: str) -> None:
        """Checks status of a single device and logs it."""
        status = self.check_device_status(device_id)
        if status != "online":
            logger.warning("ADBSupervisor: Device %s (Agent %s) is %s", device_id, agent_id, status)

    def get_mapped_device(self, agent_id: str) -> Optional[str]:
        """Returns the dynamically mapped device_id for an agent_id."""
        return self._agent_to_device_map.get(agent_id)

    def _get_device(self, device_id: str) -> Any:
        """Retrieve a specific device handle by serial number."""
        if not self._client:
            self._connect()
        if not self._client:
            raise ConnectionError("ADB server not reachable")

        devices = self._client.devices()
        for d in devices:
            if d.serial == device_id:
                return d

        raise ValueError(f"Device '{device_id}' not found among connected devices")

    # ── Automated Recovery Machine ────────────────────────────────────────

    def spoof_device_hardware(self, device_id: str, serial: str) -> bool:
        """
        Executes genuine hardware property spoofing by modifying /system/build.prop.
        Requires adb root and remount.
        """
        logger.info("ADBSupervisor [%s]: Initiating true hardware spoofing routine.", device_id)
        try:
            device = self._get_device(device_id)
            
            # 1. Gain root access and remount system partition as writable
            device.root()
            time.sleep(1) # wait for root to stabilize
            device = self._get_device(device_id) # Re-fetch after root daemon restart
            
            # Explicit remount command in case device.remount() isn't supported directly by ppadb wrapper
            device.shell("remount")
            
            # 2. Pull build.prop
            prop_content = device.shell("cat /system/build.prop")
            if not prop_content:
                logger.error("ADBSupervisor [%s]: Could not read /system/build.prop", device_id)
                return False
                
            # 3. Regex replacements for strict masking
            prop_content = re.sub(r"^ro\.product\.model=.*$", "ro.product.model=SM-G998B", prop_content, flags=re.MULTILINE)
            prop_content = re.sub(r"^ro\.product\.brand=.*$", "ro.product.brand=samsung", prop_content, flags=re.MULTILINE)
            prop_content = re.sub(r"^ro\.product\.manufacturer=.*$", "ro.product.manufacturer=samsung", prop_content, flags=re.MULTILINE)
            
            # Ensure they exist if not found by regex
            if "ro.product.model=SM-G998B" not in prop_content:
                prop_content += "\nro.product.model=SM-G998B"
            if "ro.product.brand=samsung" not in prop_content:
                prop_content += "\nro.product.brand=samsung"
            if "ro.product.manufacturer=samsung" not in prop_content:
                prop_content += "\nro.product.manufacturer=samsung"
                
            # 4. Write back to device using a temporary file
            # Push via shell echo to a tmp file, then move (ppadb push requires local file)
            # To avoid local file I/O issues in docker, we use shell echo
            device.shell("rm -f /data/local/tmp/build.prop.new")
            
            # Break into chunks to avoid argument list too long
            chunk_size = 1024
            for i in range(0, len(prop_content), chunk_size):
                chunk = prop_content[i:i+chunk_size]
                # Safe echo append
                escaped_chunk = chunk.replace("'", "'\\''")
                device.shell(f"echo -n '{escaped_chunk}' >> /data/local/tmp/build.prop.new")
                
            # Overwrite and set permissions
            device.shell("cp /data/local/tmp/build.prop.new /system/build.prop")
            device.shell("chmod 644 /system/build.prop")
            device.shell("rm /data/local/tmp/build.prop.new")
            
            # Also spoof serialno via setprop temporarily (build.prop handles the rest on reboot)
            device.shell(f'setprop ro.serialno "{serial}"')
            
            logger.info("ADBSupervisor [%s]: Hardware spoofed successfully in /system/build.prop (SM-G998B).", device_id)
            return True
        except Exception as e:
            logger.error("ADBSupervisor [%s]: True hardware spoofing failed — %s", device_id, e)
            return False

    def recover_device_state(self, device_id: str, package: str, proxy: Optional[str] = None) -> bool:
        """
        Executes terminal shell injections to recover from Appium selector failures.
        Force stops the package, optionally clears it, and enforces the OS proxy.
        """
        logger.info("ADBSupervisor [%s]: Initiating automated recovery machine for %s", device_id, package)
        try:
            device = self._get_device(device_id)
            
            # 1. Force Stop
            device.shell(f"am force-stop {package}")
            logger.info("ADBSupervisor [%s]: Recovery — am force-stop executed.", device_id)
            
            # 2. PM Clear (Optional Cache Scrub - uncomment if strict wipe needed)
            # device.shell(f"pm clear {package}")
            # logger.info("ADBSupervisor [%s]: Recovery — pm clear executed.", device_id)
            
            # 3. Re-enforce Proxy
            if proxy:
                host, port = proxy.split(":")
                self.enforce_os_level_proxy(device_id, host, int(port))
                logger.info("ADBSupervisor [%s]: Recovery — proxy re-enforced.", device_id)
            else:
                self.clear_os_proxy(device_id)
                logger.info("ADBSupervisor [%s]: Recovery — proxy cleared.", device_id)
                
            return True
        except Exception as e:
            logger.error("ADBSupervisor [%s]: Recovery machine failed — %s", device_id, e)
            return False

    # ── Device Enumeration ────────────────────────────────────────────────

    def list_connected_devices(self) -> List[Dict[str, Any]]:
        """Enumerate all devices connected to the ADB server."""
        if not self._client:
            self._connect()
        if not self._client:
            return []

        result = []
        try:
            devices = self._client.devices()
            for d in devices:
                info = {
                    "device_id": d.serial,
                    "state": "online",
                    "model": self._shell_prop(d, "ro.product.model"),
                    "android_version": self._shell_prop(d, "ro.build.version.release"),
                    "sdk_version": self._shell_prop(d, "ro.build.version.sdk"),
                }
                result.append(info)
        except Exception as e:
            logger.error("ADBSupervisor: Failed to enumerate devices — %s", e)

        return result

    def check_device_status(self, device_id: str) -> str:
        """Check the status of a specific device."""
        if not self._client:
            self._connect()
        if not self._client:
            return "adb_unavailable"

        try:
            devices = self._client.devices()
            for d in devices:
                if d.serial == device_id:
                    return "online"

            raw_output = self._client.host("devices-l")
            if device_id in raw_output:
                if "unauthorized" in raw_output:
                    return "unauthorized"
                if "offline" in raw_output:
                    return "offline"

            return "not_found"
        except Exception as e:
            logger.error("ADBSupervisor: status check failed — %s", e)
            return "error"

    # ── App Lifecycle ─────────────────────────────────────────────────────

    def launch_app_activity(
        self,
        device_id: str,
        package: str,
        activity: str,
    ) -> bool:
        """Launch an Android app by package/activity using `am start`."""
        try:
            device = self._get_device(device_id)
            cmd = f"am start -n {package}/{activity}"
            output = device.shell(cmd)
            logger.info(
                "ADBSupervisor [%s]: Launched %s/%s — %s",
                device_id, package, activity, output.strip(),
            )
            return "Error" not in output
        except Exception as e:
            logger.error(
                "ADBSupervisor [%s]: Failed to launch %s — %s",
                device_id, package, e,
            )
            return False

    def force_stop_app(self, device_id: str, package: str) -> bool:
        """Force-stop an application using `am force-stop`."""
        try:
            device = self._get_device(device_id)
            device.shell(f"am force-stop {package}")
            logger.info("ADBSupervisor [%s]: Force-stopped %s", device_id, package)
            return True
        except Exception as e:
            logger.error("ADBSupervisor [%s]: Failed to force-stop %s — %s", device_id, package, e)
            return False

    # ── OS-Level Proxy ────────────────────────────────────────────────────

    def enforce_os_level_proxy(
        self,
        device_id: str,
        proxy_host: str,
        proxy_port: int,
    ) -> bool:
        """Set a global HTTP proxy at the Android OS level."""
        try:
            device = self._get_device(device_id)
            proxy_str = f"{proxy_host}:{proxy_port}"
            device.shell(f"settings put global http_proxy {proxy_str}")
            logger.info("ADBSupervisor [%s]: OS-level proxy set to %s", device_id, proxy_str)
            return True
        except Exception as e:
            logger.error("ADBSupervisor [%s]: Failed to set proxy — %s", device_id, e)
            return False

    def clear_os_proxy(self, device_id: str) -> bool:
        """Remove the OS-level proxy setting."""
        try:
            device = self._get_device(device_id)
            device.shell("settings put global http_proxy :0")
            logger.info("ADBSupervisor [%s]: OS-level proxy cleared", device_id)
            return True
        except Exception as e:
            logger.error("ADBSupervisor [%s]: Failed to clear proxy — %s", device_id, e)
            return False

    # ── Device Telemetry ──────────────────────────────────────────────────

    def get_device_info(self, device_id: str) -> Dict[str, Any]:
        """Collect comprehensive device telemetry."""
        try:
            device = self._get_device(device_id)

            battery_output = device.shell("dumpsys battery")
            battery_level = self._parse_battery_level(battery_output)

            loadavg = device.shell("cat /proc/loadavg").strip()
            cpu_load_1m = float(loadavg.split()[0]) if loadavg else 0.0

            current_proxy = device.shell("settings get global http_proxy").strip()
            if current_proxy in ("null", ":0", ""):
                current_proxy = None

            meminfo = device.shell("cat /proc/meminfo")
            mem_total, mem_free = self._parse_meminfo(meminfo)

            screen_state = device.shell("dumpsys power | grep 'Display Power'").strip()
            screen_on = "ON" in screen_state.upper() if screen_state else False

            return {
                "device_id": device_id,
                "state": "online",
                "model": self._shell_prop(device, "ro.product.model"),
                "manufacturer": self._shell_prop(device, "ro.product.manufacturer"),
                "android_version": self._shell_prop(device, "ro.build.version.release"),
                "sdk_version": self._shell_prop(device, "ro.build.version.sdk"),
                "battery_level": battery_level,
                "cpu_load_1m": cpu_load_1m,
                "current_proxy": current_proxy,
                "mem_total_mb": mem_total,
                "mem_free_mb": mem_free,
                "screen_on": screen_on,
            }
        except Exception as e:
            logger.error("ADBSupervisor [%s]: Failed to get device info — %s", device_id, e)
            return {"device_id": device_id, "state": "error", "error": str(e)}

    # ── Internal Helpers ──────────────────────────────────────────────────

    @staticmethod
    def _shell_prop(device: Any, prop: str) -> str:
        try:
            return device.shell(f"getprop {prop}").strip()
        except Exception:
            return "unknown"

    @staticmethod
    def _parse_battery_level(dumpsys_output: str) -> int:
        match = re.search(r"level:\s*(\d+)", dumpsys_output)
        return int(match.group(1)) if match else -1

    @staticmethod
    def _parse_meminfo(meminfo: str) -> Tuple[int, int]:
        total = free = 0
        for line in meminfo.splitlines():
            if line.startswith("MemTotal:"):
                match = re.search(r"(\d+)", line)
                if match:
                    total = int(match.group(1)) // 1024
            elif line.startswith("MemAvailable:"):
                match = re.search(r"(\d+)", line)
                if match:
                    free = int(match.group(1)) // 1024
        return total, free
