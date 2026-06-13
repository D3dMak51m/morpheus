"""
MYRMIDON — SMS Gateway Client (Stage 24)
==========================================
Async client for an SMS-Activate-compatible virtual-number API, used by the
Clone Factory to purchase disposable numbers and intercept registration OTPs.

Protocol (SMS-Activate `handler_api.php`):
  • getNumber  → "ACCESS_NUMBER:<activationId>:<phone>"
  • getStatus  → "STATUS_WAIT_CODE" | "STATUS_OK:<code>" | "STATUS_CANCEL" | ...
  • setStatus  → status 8 = cancel, 6 = finish (mark number reusable/complete)

Anti-fraud / timeout posture:
  • The API key is loaded strictly from the environment (SMS_API_KEY). With no
    key every call raises SmsGatewayError("NO_KEY") — we never fabricate numbers.
  • wait_for_sms polls on a bounded schedule and ALWAYS cancels the activation on
    timeout so a burned number is released rather than silently abandoned (a
    half-open registration is a strong anti-fraud signal).
  • All provider error codes (NO_NUMBERS, NO_BALANCE, BAD_KEY, …) are surfaced as
    typed exceptions so the orchestrator can fail that bot cleanly and continue.

This is a real HTTP client — no mocks.
"""

import asyncio
import logging
import os
from typing import Tuple

import httpx

logger = logging.getLogger("myrmidon.sms_gateway")

SMS_API_KEY = os.getenv("SMS_API_KEY", "")
SMS_API_BASE = os.getenv("SMS_API_BASE", "https://api.sms-activate.org/stubs/handler_api.php")
SMS_DEFAULT_COUNTRY = os.getenv("SMS_DEFAULT_COUNTRY", "0")  # 0 = Russia in SMS-Activate
SMS_WAIT_TIMEOUT_SEC = int(os.getenv("SMS_WAIT_TIMEOUT_SEC", "180"))

# Map our platform names to SMS-Activate short service codes.
SERVICE_CODES = {
    "instagram": "ig",
    "telegram": "tg",
    "twitter": "tw",
    "x": "tw",
    "threads": "ig",      # Threads registers against the Instagram/Meta identity
    "youtube": "go",      # Google account
    "google": "go",
    "facebook": "fb",
    "whatsapp": "wa",
}

# Provider status strings that mean "no code yet — keep polling".
_PENDING_STATUSES = {"STATUS_WAIT_CODE", "STATUS_WAIT_RETRY", "STATUS_WAIT_RESEND"}


class SmsGatewayError(Exception):
    """Raised on any non-recoverable SMS provider condition."""


def _require_key() -> None:
    if not SMS_API_KEY:
        raise SmsGatewayError(
            "NO_KEY: SMS_API_KEY is not configured. Set it in .env to enable "
            "autonomous number provisioning."
        )


def service_code(service: str) -> str:
    """Resolve a platform name to its SMS-Activate service code."""
    return SERVICE_CODES.get((service or "").strip().lower(), (service or "ig").strip().lower())


async def _call(params: dict) -> str:
    """Issue one GET to the provider and return the raw text body."""
    _require_key()
    query = {"api_key": SMS_API_KEY, **params}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(SMS_API_BASE, params=query)
        resp.raise_for_status()
        return resp.text.strip()


async def buy_number(service: str, country: str | None = None) -> Tuple[str, str]:
    """
    Purchase a disposable number for ``service``.

    Returns (activation_id, phone_number). Raises SmsGatewayError on any provider
    error (NO_NUMBERS / NO_BALANCE / BAD_KEY / unexpected response).
    """
    code = service_code(service)
    body = await _call({
        "action": "getNumber",
        "service": code,
        "country": country or SMS_DEFAULT_COUNTRY,
    })

    if body.startswith("ACCESS_NUMBER"):
        parts = body.split(":")
        if len(parts) >= 3:
            activation_id, phone = parts[1], parts[2]
            logger.info("SMS: bought number %s (activation=%s, service=%s).", phone, activation_id, code)
            return activation_id, phone
        raise SmsGatewayError(f"Malformed getNumber response: {body!r}")

    # Provider error codes arrive as bare strings.
    raise SmsGatewayError(f"buy_number failed for service '{code}': {body}")


async def wait_for_sms(activation_id: str, timeout: int | None = None, poll_interval: float = 5.0) -> str:
    """
    Poll for the OTP belonging to ``activation_id``.

    Blocks (cooperatively) up to ``timeout`` seconds. On timeout the activation
    is cancelled before raising, so the number is never left half-registered.
    """
    deadline = asyncio.get_event_loop().time() + (timeout or SMS_WAIT_TIMEOUT_SEC)

    while asyncio.get_event_loop().time() < deadline:
        body = await _call({"action": "getStatus", "id": activation_id})

        if body.startswith("STATUS_OK"):
            code = body.split(":", 1)[1] if ":" in body else ""
            logger.info("SMS: OTP received for activation %s.", activation_id)
            return code.strip()

        if body in _PENDING_STATUSES:
            await asyncio.sleep(poll_interval)
            continue

        if body == "STATUS_CANCEL":
            raise SmsGatewayError(f"Activation {activation_id} was cancelled by the provider.")

        # Unknown/transient — keep polling but log it.
        logger.warning("SMS: unexpected status for %s: %s", activation_id, body)
        await asyncio.sleep(poll_interval)

    # Timed out — release the number to avoid a burned, half-open registration.
    try:
        await cancel_activation(activation_id)
    except Exception as exc:
        logger.warning("SMS: failed to cancel timed-out activation %s: %s", activation_id, exc)
    raise SmsGatewayError(f"Timed out waiting for SMS on activation {activation_id}.")


async def cancel_activation(activation_id: str) -> bool:
    """Cancel an activation (setStatus=8) so the number is released."""
    body = await _call({"action": "setStatus", "status": "8", "id": activation_id})
    ok = body in ("ACCESS_CANCEL", "ACCESS_ACTIVATION")
    logger.info("SMS: cancel activation %s → %s", activation_id, body)
    return ok


async def finish_activation(activation_id: str) -> bool:
    """Mark an activation complete (setStatus=6) after a successful registration."""
    body = await _call({"action": "setStatus", "status": "6", "id": activation_id})
    logger.info("SMS: finish activation %s → %s", activation_id, body)
    return body == "ACCESS_ACTIVATION"
