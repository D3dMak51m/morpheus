"""
HUGINN — Authenticated Scouting Engine (Stage 18)
===================================================
Active, authenticated social-media scouting. Instead of passively parsing RSS,
this engine impersonates legitimate mobile apps using real `auth_cookies` pulled
from DAEDALUS, hits the platforms' private API endpoints, and ranks posts by
engagement *velocity* (virality). Hot targets are pushed to DAEDALUS's Scouting
Radar.

OpSec:
  - `curl_cffi` spoofs the JA3/TLS fingerprint of a real client (impersonate).
  - Per-account SOCKS/HTTP proxy isolation (rotation across sessions).
  - Mobile API endpoints + authentic app headers to bypass JS-hydration locks.

Velocity Metric:
    engagement      = likes + comments (+ reposts where available)
    hours_since     = max(0.5, (now - posted_at) / 3600)
    velocity_score  = engagement / hours_since
    VIRAL when velocity_score >= VELOCITY_THRESHOLD
"""

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx
from curl_cffi import requests

logger = logging.getLogger("huginn.scrapers.scouting_engine")

DAEDALUS_URL = os.getenv("DAEDALUS_URL", "http://daedalus:8000")
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "morpheus-internal-sync-key")

VELOCITY_THRESHOLD = float(os.getenv("VELOCITY_THRESHOLD", "300"))
SCOUTING_POLL_SEC = int(os.getenv("SCOUTING_POLL_SEC", "180"))

# Telegram channel virality is measured in views/forwards/reactions, not
# likes+comments, so it warrants a far lower floor than the social-API platforms.
TG_VELOCITY_THRESHOLD = float(os.getenv("TG_VELOCITY_THRESHOLD", "1"))
TG_SCOUT_DIALOG_LIMIT = int(os.getenv("TG_SCOUT_DIALOG_LIMIT", "30"))
TG_SCOUT_POSTS_PER_CHANNEL = int(os.getenv("TG_SCOUT_POSTS_PER_CHANNEL", "15"))
# Cap targets surfaced per cycle so the radar shows the hottest, not every post.
TG_SCOUT_MAX_TARGETS = int(os.getenv("TG_SCOUT_MAX_TARGETS", "40"))

# Per-platform viral floor (falls back to VELOCITY_THRESHOLD when unset).
PLATFORM_VELOCITY_THRESHOLD: Dict[str, float] = {
    "telegram": TG_VELOCITY_THRESHOLD,
}

# Telegram MTProto credentials (shared with the Auth Factory / mission driver).
TG_API_ID = int(os.getenv("TG_API_ID", "0") or 0)
TG_API_HASH = os.getenv("TG_API_HASH", "")

# Public web bearer used by x.com's own client for authenticated GraphQL/v1.1 reads.
X_PUBLIC_BEARER = (
    "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D"
    "1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)
# Instagram private mobile API app id (web).
IG_APP_ID = os.getenv("IG_APP_ID", "936619743392459")
IG_MOBILE_UA = (
    "Instagram 269.0.0.18.75 Android (30/11; 420dpi; 1080x2400; "
    "samsung; SM-G991B; o1s; exynos2100; en_US; 314665256)"
)


# ── DAEDALUS interchange ──────────────────────────────────────────────────

async def _fetch_sessions(client: httpx.AsyncClient) -> List[Dict[str, Any]]:
    """Pull active authenticated social sessions (cookies + proxy) from DAEDALUS."""
    try:
        resp = await client.get(
            f"{DAEDALUS_URL}/api/v1/scouting/internal/sessions",
            headers={"X-Internal-Token": INTERNAL_API_TOKEN},
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json().get("sessions", [])
    except Exception as exc:
        logger.error("Failed to fetch scouting sessions from DAEDALUS: %s", exc)
        return []


async def _push_hot_target(client: httpx.AsyncClient, target: Dict[str, Any]) -> None:
    """Register a viral discovery on the DAEDALUS Scouting Radar."""
    try:
        resp = await client.post(
            f"{DAEDALUS_URL}/api/v1/scouting/hot-targets",
            json=target,
            headers={"X-Internal-Token": INTERNAL_API_TOKEN},
            timeout=10.0,
        )
        resp.raise_for_status()
        logger.info(
            "VIRAL pushed → %s (velocity=%.1f) %s",
            target["platform"], target["velocity_score"], target["url"],
        )
    except Exception as exc:
        logger.error("Failed to push hot target to DAEDALUS: %s", exc)


# ── Velocity ──────────────────────────────────────────────────────────────

def compute_velocity(engagement: int, posted_at: Optional[int]) -> float:
    """engagement / hours-since-posted, with a 30-min floor to avoid div blow-up."""
    now = int(time.time())
    if not posted_at or posted_at <= 0 or posted_at > now:
        hours = 1.0
    else:
        hours = max(0.5, (now - posted_at) / 3600.0)
    return round(engagement / hours, 2)


def _proxies(session: Dict[str, Any]) -> Optional[Dict[str, str]]:
    proxy = session.get("assigned_proxy")
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


# ── Instagram (private mobile API) ────────────────────────────────────────

async def scout_instagram(session: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Read the authenticated home timeline via Instagram's private mobile API.
    Requires `sessionid` (+ usually ds_user_id, csrftoken) in auth_cookies.
    """
    cookies = session.get("auth_cookies") or {}
    if "sessionid" not in cookies:
        logger.warning("IG session for %s missing 'sessionid' cookie — skipping.", session.get("username"))
        return []

    headers = {
        "User-Agent": IG_MOBILE_UA,
        "X-IG-App-ID": IG_APP_ID,
        "X-IG-Capabilities": "3brTvw==",
        "Accept": "*/*",
        "Accept-Language": "en-US",
    }
    discoveries: List[Dict[str, Any]] = []
    try:
        async with requests.AsyncSession(
            impersonate="chrome",
            cookies=cookies,
            headers=headers,
            proxies=_proxies(session),
        ) as s:
            resp = await s.post(
                "https://i.instagram.com/api/v1/feed/timeline/",
                data={"reason": "cold_start_fetch", "is_pull_to_refresh": "0"},
                timeout=20,
            )
            if resp.status_code != 200:
                logger.warning("IG timeline returned %d for %s", resp.status_code, session.get("username"))
                return []

            payload = resp.json()
            for item in payload.get("feed_items", []):
                media = item.get("media_or_ad")
                if not media:
                    continue
                likes = int(media.get("like_count", 0) or 0)
                comments = int(media.get("comment_count", 0) or 0)
                taken_at = media.get("taken_at")
                code = media.get("code")
                caption = (media.get("caption") or {}).get("text", "") if media.get("caption") else ""
                author = ((media.get("user") or {}).get("username")) or "unknown"
                if not code:
                    continue
                engagement = likes + comments
                discoveries.append({
                    "platform": "instagram",
                    "url": f"https://www.instagram.com/p/{code}/",
                    "author_name": author,
                    "content_summary": caption[:500],
                    "engagement": engagement,
                    "posted_at": int(taken_at) if taken_at else None,
                })
    except Exception as exc:
        logger.error("IG scouting error for %s: %s", session.get("username"), exc)
    return discoveries


# ── X / Twitter (authenticated v1.1 home timeline) ────────────────────────

async def scout_x(session: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Read the authenticated home timeline via x.com's v1.1 endpoint.
    Requires `auth_token` and `ct0` (csrf) in auth_cookies.
    """
    cookies = session.get("auth_cookies") or {}
    ct0 = cookies.get("ct0")
    if "auth_token" not in cookies or not ct0:
        logger.warning("X session for %s missing auth_token/ct0 — skipping.", session.get("username"))
        return []

    headers = {
        "Authorization": X_PUBLIC_BEARER,
        "x-csrf-token": ct0,
        "x-twitter-auth-type": "OAuth2Session",
        "x-twitter-active-user": "yes",
        "Accept": "application/json",
    }
    discoveries: List[Dict[str, Any]] = []
    try:
        async with requests.AsyncSession(
            impersonate="chrome",
            cookies=cookies,
            headers=headers,
            proxies=_proxies(session),
        ) as s:
            resp = await s.get(
                "https://api.twitter.com/1.1/statuses/home_timeline.json?count=50&tweet_mode=extended",
                timeout=20,
            )
            if resp.status_code != 200:
                logger.warning("X timeline returned %d for %s", resp.status_code, session.get("username"))
                return []

            for tw in resp.json():
                likes = int(tw.get("favorite_count", 0) or 0)
                rts = int(tw.get("retweet_count", 0) or 0)
                replies = int(tw.get("reply_count", 0) or 0)
                engagement = likes + rts + replies
                user = (tw.get("user") or {})
                screen = user.get("screen_name", "unknown")
                tid = tw.get("id_str")
                text = tw.get("full_text") or tw.get("text") or ""
                posted_at = _parse_twitter_time(tw.get("created_at"))
                if not tid:
                    continue
                discoveries.append({
                    "platform": "x",
                    "url": f"https://x.com/{screen}/status/{tid}",
                    "author_name": screen,
                    "content_summary": text[:500],
                    "engagement": engagement,
                    "posted_at": posted_at,
                })
    except Exception as exc:
        logger.error("X scouting error for %s: %s", session.get("username"), exc)
    return discoveries


def _parse_twitter_time(created_at: Optional[str]) -> Optional[int]:
    """Parse Twitter's 'Wed Oct 10 20:19:24 +0000 2018' format → epoch seconds."""
    if not created_at:
        return None
    try:
        from datetime import datetime
        dt = datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y")
        return int(dt.timestamp())
    except Exception:
        return None


# ── Telegram (authenticated MTProto via the agent's own session) ──────────

async def scout_telegram(session: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Scout the channels/supergroups the agent's Telegram account is subscribed to.

    Uses the account's Pyrogram session string (same one MYRMIDON executes with)
    to read recent posts and rank them by engagement velocity. This is what lets
    the operator's *registered Telegram account* light up the Scouting Radar.

    Concurrency note: this opens the account's session briefly. Mission execution
    (MYRMIDON) uses the same session, so the engine keeps the connection
    short-lived and fully fail-soft to avoid clashing with an in-flight mission.
    """
    if not TG_API_ID or not TG_API_HASH:
        logger.warning("Telegram scouting skipped — TG_API_ID/TG_API_HASH not configured.")
        return []

    cookies = session.get("auth_cookies") or {}
    session_string = cookies.get("session_string") if isinstance(cookies, dict) else cookies
    if not session_string:
        logger.warning("Telegram session for %s has no session_string — skipping.", session.get("username"))
        return []

    try:
        from pyrogram import Client
        from pyrogram.enums import ChatType
    except Exception as exc:  # pragma: no cover - import guard
        logger.error("Pyrogram unavailable in HUGINN — cannot scout Telegram: %s", exc)
        return []

    label = session.get("agent_id") or session.get("username") or "tg"
    discoveries: List[Dict[str, Any]] = []
    app = Client(
        f"scout_{label}",
        api_id=TG_API_ID,
        api_hash=TG_API_HASH,
        session_string=session_string,
        in_memory=True,
    )
    try:
        async with app:
            async for dialog in app.get_dialogs(limit=TG_SCOUT_DIALOG_LIMIT):
                chat = dialog.chat
                if chat.type not in (ChatType.CHANNEL, ChatType.SUPERGROUP):
                    continue
                try:
                    async for msg in app.get_chat_history(chat.id, limit=TG_SCOUT_POSTS_PER_CHANNEL):
                        text = (msg.text or msg.caption or "").strip()
                        if not text:
                            continue
                        views = int(getattr(msg, "views", 0) or 0)
                        forwards = int(getattr(msg, "forwards", 0) or 0)
                        reactions = 0
                        if getattr(msg, "reactions", None) and msg.reactions.reactions:
                            reactions = sum(int(r.count or 0) for r in msg.reactions.reactions)
                        # Forwards and reactions are stronger virality signals than
                        # passive views, so they are weighted higher.
                        engagement = views + forwards * 5 + reactions * 3
                        if engagement <= 0:
                            continue
                        if chat.username:
                            url = f"https://t.me/{chat.username}/{msg.id}"
                        else:
                            internal = str(chat.id).replace("-100", "", 1).lstrip("-")
                            url = f"https://t.me/c/{internal}/{msg.id}"
                        discoveries.append({
                            "platform": "telegram",
                            "url": url,
                            "author_name": chat.title or chat.username or str(chat.id),
                            "content_summary": text[:500],
                            "engagement": engagement,
                            "posted_at": int(msg.date.timestamp()) if msg.date else None,
                        })
                except Exception as exc:
                    logger.warning("Telegram scouting: failed to read %s: %s", chat.title or chat.id, exc)
    except Exception as exc:
        logger.error("Telegram scouting error for %s: %s", session.get("username"), exc)

    # Surface only the hottest posts this cycle (the radar ranks by velocity too).
    discoveries.sort(key=lambda d: d["engagement"], reverse=True)
    return discoveries[:TG_SCOUT_MAX_TARGETS]


PLATFORM_SCOUTS = {
    "instagram": scout_instagram,
    "twitter": scout_x,
    "x": scout_x,
    "telegram": scout_telegram,
}


# ── Engine loop ───────────────────────────────────────────────────────────

async def run_scouting_engine() -> None:
    """
    Main async scouting loop: fetch authenticated sessions, scrape each
    platform's private feed, compute velocity, and push VIRAL targets.
    Designed to initialize cleanly even with zero cookies configured.
    """
    logger.info(
        "Authenticated Scouting Engine started (threshold=%.0f, poll=%ds).",
        VELOCITY_THRESHOLD, SCOUTING_POLL_SEC,
    )
    async with httpx.AsyncClient() as daedalus:
        while True:
            try:
                sessions = await _fetch_sessions(daedalus)
                if not sessions:
                    logger.info("No authenticated social sessions available yet — awaiting operator cookies.")
                else:
                    logger.info("Scouting across %d authenticated session(s).", len(sessions))

                for session in sessions:
                    platform = (session.get("platform") or "").lower()
                    scout = PLATFORM_SCOUTS.get(platform)
                    if not scout:
                        continue

                    threshold = PLATFORM_VELOCITY_THRESHOLD.get(platform, VELOCITY_THRESHOLD)
                    discoveries = await scout(session)
                    for d in discoveries:
                        velocity = compute_velocity(d["engagement"], d.get("posted_at"))
                        if velocity < threshold:
                            continue
                        d["velocity_score"] = velocity
                        await _push_hot_target(daedalus, d)

            except asyncio.CancelledError:
                logger.info("Scouting Engine cancelled — shutting down.")
                raise
            except Exception as exc:
                logger.exception("Scouting Engine loop error: %s", exc)

            await asyncio.sleep(SCOUTING_POLL_SEC)
