"""
MYRMIDON — Autonomous Registration Driver (Stage 24)
======================================================
Drives a fresh AVD through a full account registration entirely via the hardened
coordinate-based `human_type()` engine (ZERO send_keys) so the flow is
indistinguishable from a human and survives anti-fraud heuristics.

Flow (per the Clone Factory mandate):
    open target app → enter phone (from the SMS gateway) → poll wait_for_sms →
    enter OTP → enter generated name/password → extract_session_state() →
    return the session payload.

The SMS interception (async) and the physical typing (blocking Appium) are
interleaved by ``run_auto_registration``: each driver step runs in a threadpool
while the OTP wait awaits the gateway, keeping the FastAPI event loop free.
"""

import logging
import random
import string
import time
from typing import Any, Dict, Optional

from fastapi.concurrency import run_in_threadpool
from selenium.webdriver.common.by import By

from app.drivers.mobile_base import BaseMobileDriver
from app import sms_gateway

logger = logging.getLogger("myrmidon.drivers.registration")

# Package / launch activity per platform (matches Auth Factory's mapping).
PLATFORM_APPS = {
    "instagram": ("com.instagram.android", "com.instagram.android.activity.MainTabActivity"),
    "telegram": ("org.telegram.messenger", "org.telegram.ui.LaunchActivity"),
    "twitter": ("com.twitter.android", "com.twitter.app.main.MainActivity"),
    "threads": ("com.instagram.barcelona", "com.instagram.barcelona.MainActivity"),
    "youtube": ("com.google.android.youtube", "com.google.android.youtube.HomeActivity"),
}

# Localised affordance labels for "advance" buttons (UiAutomator text match).
_NEXT_LABELS = ["Next", "Continue", "Done", "Sign up", "Log in", "Далее", "Продолжить", "Keyingi", "Davom"]


def _gen_password(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%"
    return "".join(random.choice(alphabet) for _ in range(length))


class RegistrationDriver(BaseMobileDriver):
    """A BaseMobileDriver specialised for first-run account registration."""

    def __init__(self, agent_id: str, device_id: str, package: str):
        super().__init__(agent_id, {}, device_id=device_id)
        self.package = package
        self.log: list[str] = []

    # ── low-level helpers ──────────────────────────────────────────────────

    def _edit_texts(self):
        return self.driver.find_elements(By.CLASS_NAME, "android.widget.EditText")

    def _tap_advance(self) -> bool:
        """Tap the first visible 'next/continue' affordance; return success."""
        for label in _NEXT_LABELS:
            try:
                el = self.driver.find_element(
                    By.ANDROID_UIAUTOMATOR,
                    f'new UiSelector().textContains("{label}")',
                )
                el.click()
                self.log.append(f"[OK] Tapped advance affordance '{label}'.")
                time.sleep(1.5)
                return True
            except Exception:
                continue
        self.log.append("[..] No labelled advance button found; relying on IME action.")
        return False

    # ── flow steps (each runs inside a threadpool) ─────────────────────────

    def prepare(self) -> None:
        self.log.append(f"[{self.agent_id}] Starting session on '{self.device_id}'…")
        self.start_session()
        try:
            self.driver.activate_app(self.package)
            self.log.append(f"[OK] Activated {self.package}.")
        except Exception as e:
            self.log.append(f"[..] activate_app failed ({e}); assuming app is foregrounded.")
        time.sleep(3.0)

    def enter_phone(self, phone: str) -> bool:
        fields = self._edit_texts()
        if not fields:
            self.log.append("[FAIL] No phone input field found on the registration screen.")
            return False
        self.human_type(fields[0], phone)
        self.log.append(f"[OK] Entered phone {phone} via coordinate typing.")
        self._tap_advance()
        return True

    def enter_otp(self, code: str) -> bool:
        fields = self._edit_texts()
        if not fields:
            self.log.append("[FAIL] No OTP input field found.")
            return False
        self.human_type(fields[0], code)
        self.log.append("[OK] Entered OTP via coordinate typing.")
        self._tap_advance()
        return True

    def enter_credentials(self, full_name: str, password: str) -> None:
        fields = self._edit_texts()
        # Heuristic: first remaining EditText = name, last = password.
        if fields:
            self.human_type(fields[0], full_name)
            self.log.append(f"[OK] Entered display name '{full_name}'.")
        if len(fields) > 1:
            self.human_type(fields[-1], password)
            self.log.append("[OK] Entered generated password.")
        self._tap_advance()
        time.sleep(3.0)

    def capture(self) -> Dict[str, Any]:
        self.log.append("[..] Extracting session state…")
        session = self.extract_session_state(package_name=self.package)
        self.log.append("[OK] Session state captured.")
        return session


async def run_auto_registration(
    device_id: str,
    platform: str,
    agent_id: str,
    full_name: Optional[str] = None,
    service: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Execute the full autonomous registration and return a structured result:
    {success, phone, activation_id, session, password, log, error?}.

    Interleaves async SMS interception with blocking Appium typing. Always
    cancels/finishes the SMS activation so no number is left half-open.
    """
    package = PLATFORM_APPS.get(platform.lower(), (platform, ""))[0]
    full_name = full_name or f"{agent_id.replace('_', ' ').title()}"
    password = _gen_password()
    result: Dict[str, Any] = {
        "success": False, "phone": None, "activation_id": None,
        "session": None, "password": password, "log": [],
    }

    driver = RegistrationDriver(agent_id, device_id, package)
    activation_id: Optional[str] = None
    try:
        # 1. Purchase a disposable number.
        activation_id, phone = await sms_gateway.buy_number(service or platform)
        result["activation_id"] = activation_id
        result["phone"] = phone

        # 2. Open app + enter the phone number (blocking → threadpool).
        await run_in_threadpool(driver.prepare)
        if not await run_in_threadpool(driver.enter_phone, phone):
            raise RuntimeError("Failed to enter phone number.")

        # 3. Await the OTP (async, bounded, self-cancelling on timeout).
        code = await sms_gateway.wait_for_sms(activation_id)
        if not await run_in_threadpool(driver.enter_otp, code):
            raise RuntimeError("Failed to enter OTP.")

        # 4. Fill display name + password.
        await run_in_threadpool(driver.enter_credentials, full_name, password)

        # 5. Extract the live session.
        session = await run_in_threadpool(driver.capture)
        result["session"] = session

        # 6. Mark the number finished so it is not re-billed.
        try:
            await sms_gateway.finish_activation(activation_id)
        except Exception:
            pass

        captured = sum(len(session.get(k, {})) for k in ("cookies", "local_storage", "shared_prefs"))
        result["success"] = captured > 0
        if not result["success"]:
            result["error"] = "Registration completed but no session data was captured."
    except Exception as e:
        logger.error("Auto-registration failed on %s (%s): %s", device_id, platform, e)
        result["error"] = str(e)
        if activation_id:
            try:
                await sms_gateway.cancel_activation(activation_id)
            except Exception:
                pass
    finally:
        result["log"] = driver.log
        await run_in_threadpool(driver.close_session)

    return result
