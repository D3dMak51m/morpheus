"""
MYRMIDON — Target health (can we actually comment there at all?)
================================================================
Stage 38. A mission target can be perfectly ``active`` and still be impossible to
work: the channel has comments disabled, has no linked discussion group, the
account isn't allowed to write in that group, or the reference doesn't resolve.

Before this module those failures were invisible: the engine kept spending its
scan budget on the target every 300 s, every attempt was logged as a bare
``FAILED``, and the operator saw a live mission that never posts anything.
(Measured on the live stand: 3/3 attempts on `@Match_TV` failed —
``CHAT_GUEST_SEND_FORBIDDEN`` and "post has no discussion thread".)

Now every failure is classified once and reported to DAEDALUS
(``/missions/internal/target-health``); ``blocked`` targets are skipped by the
engine until the re-check window expires, so a broken target degrades loudly
instead of silently eating the mission.
"""

import logging
import os
import time
from typing import Optional, Tuple

import httpx

logger = logging.getLogger("myrmidon.target_health")

DAEDALUS_URL = os.getenv("DAEDALUS_URL", "http://daedalus:8000")
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "morpheus-internal-sync-key")

# How long a 'blocked' verdict stands before the engine tries the target again
# (a channel can open its comments later — we must not blacklist it forever).
BLOCKED_RECHECK_SEC = int(os.getenv("TARGET_BLOCKED_RECHECK_SEC", str(6 * 3600)))

# ── Failure classification ────────────────────────────────────────────────
# (marker in the error text) -> (health, operator-facing Russian reason)
# Permanent = the target cannot be commented on at all until something changes
# on THEIR side (or the account joins/gets unbanned).

_PERMANENT = (
    ("CHAT_WRITE_FORBIDDEN", "аккаунту запрещено писать в этой группе"),
    ("USER_BANNED_IN_CHANNEL", "аккаунт забанен в этом канале/группе"),
    ("CHANNEL_PRIVATE", "канал закрытый — аккаунт не имеет доступа"),
    ("USERNAME_NOT_OCCUPIED", "канала с таким @username не существует"),
    ("USERNAME_INVALID", "некорректный @username канала"),
    ("Peer id invalid", "ссылка на канал не резолвится этим аккаунтом"),
    ("PEER_ID_INVALID", "ссылка на канал не резолвится этим аккаунтом"),
    ("failed to join discussion group", "не удалось вступить в группу обсуждения"),
)

# Transient = worth retrying later; the target stays in play.
_TRANSIENT = (
    ("FLOOD_WAIT", "временное ограничение Telegram (FloodWait)"),
    ("SLOWMODE_WAIT", "в группе включён медленный режим"),
    ("CHAT_GUEST_SEND_FORBIDDEN", "гостям нельзя писать — аккаунт вступает в группу"),
    ("Timeout", "таймаут при обращении к Telegram"),
    ("TIMEOUT", "таймаут при обращении к Telegram"),
)

# POST-level, not target-level: a single post can have comments switched off while
# the channel as a whole is perfectly commentable. Blocking the whole target on this
# would kill a working channel because of one post.
_POST_LEVEL = (
    ("no discussion thread", "у этого поста выключены комментарии"),
    ("MSG_ID_INVALID", "у этого поста выключены комментарии"),
    ("MsgIdInvalid", "у этого поста выключены комментарии"),
)


def classify(error_text: str) -> Tuple[str, str, str]:
    """
    Map a raw Telegram/driver error into ``(health, reason_ru, scope)``.

    ``scope`` is ``'target'`` (the channel itself) or ``'post'`` (only this post) —
    a post with comments disabled must never blacklist the whole channel.
    Unknown errors are ``degraded``, never ``blocked``: a target is not written off
    because of an error we don't understand yet.
    """
    text = str(error_text or "")
    low = text.lower()
    for marker, reason in _POST_LEVEL:
        if marker.lower() in low:
            return "ok", reason, "post"
    for marker, reason in _PERMANENT:
        if marker.lower() in low:
            return "blocked", reason, "target"
    for marker, reason in _TRANSIENT:
        if marker.lower() in low:
            return "degraded", reason, "target"
    return "degraded", f"ошибка публикации: {text[:180]}", "target"


def report(identifier: str, health: str, reason: str = "",
           mission_id: Optional[int] = None) -> bool:
    """Tell DAEDALUS what we learned about this target (best-effort)."""
    if not identifier:
        return False
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{DAEDALUS_URL}/api/v1/missions/internal/target-health",
                json={"mission_id": mission_id, "identifier": identifier,
                      "health": health, "reason": reason},
                headers={"X-Internal-Token": INTERNAL_API_TOKEN},
            )
            resp.raise_for_status()
        logger.info("target_health: %s → %s (%s)", identifier, health, reason[:80])
        return True
    except Exception as exc:
        logger.warning("target_health: could not report %s: %s", identifier, exc)
        return False


def report_failure(identifier: str, error_text: str,
                   mission_id: Optional[int] = None) -> Tuple[str, str, str]:
    """
    Classify a failure and report it when it concerns the TARGET.

    Post-level problems (comments disabled on that one post) are returned to the
    caller for logging but never change the channel's health.
    Returns ``(health, reason, scope)``.
    """
    health, reason, scope = classify(error_text)
    if scope == "target":
        report(identifier, health, reason, mission_id)
    return health, reason, scope


def report_success(identifier: str, mission_id: Optional[int] = None) -> None:
    """A comment landed — the target is provably workable."""
    report(identifier, "ok", "комментарий успешно опубликован", mission_id)


def should_skip(health: str, checked_at_epoch: Optional[float]) -> bool:
    """
    Should the engine skip this target this cycle?

    Only ``blocked`` targets are skipped, and only while the re-check window holds —
    after it expires we try once more (channels do open their comments later).
    """
    if health != "blocked":
        return False
    if not checked_at_epoch:
        return True
    return (time.time() - checked_at_epoch) < BLOCKED_RECHECK_SEC


def url_channel_ref(target_url: str) -> str:
    """Extract the canonical channel ref (@name / -100…) from a post URL."""
    s = (target_url or "").strip()
    for pref in ("https://t.me/", "http://t.me/", "t.me/"):
        if s.lower().startswith(pref):
            s = s[len(pref):]
            break
    s = s.strip("/")
    if s.startswith("c/"):
        parts = s.split("/")
        return f"-100{parts[1]}" if len(parts) > 1 and parts[1].isdigit() else s
    head = s.split("/")[0]
    return f"@{head}" if head and not head.lstrip("-").isdigit() else head
