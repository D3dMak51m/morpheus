"""
DAEDALUS — SIMULATION landscape & import (fills the polygon with real texture)
==============================================================================
Two ways to populate an isolated simulation world:

**Ландшафт-скрапинг** — pull an external environment in from the open web:
  ``rss``        RSS/Atom feed → one sim channel, one sim post per entry.
  ``web``        a single page  → one sim post (title + extracted text).
  ``tg_preview`` a PUBLIC Telegram web preview (``t.me/s/<channel>``) → posts with
                 their text, photos and links. Read-only HTTP; **no account, no
                 session, no MTProto** — it cannot act on the channel, only read
                 what any anonymous browser sees.

**Импорт из основной системы** — copy production rows into the world (SELECT
only; the production row is never modified):
  knowledge facts, scraping-landscape sources, channel profiles, agent souls and
  mission definitions (as editable simulation templates).

Everything lands in ``sim_*`` tables, so imported material can be freely edited or
deleted without any production consequence.
"""

from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from sqlalchemy.orm import Session

from app.models_simulation import (
    SimChannel,
    SimKnowledge,
    SimMission,
    SimPersona,
    SimPost,
)

logger = logging.getLogger("daedalus.sim_landscape")

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0 Safari/537.36"
)
FETCH_TIMEOUT = 25.0

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def strip_html(raw: str) -> str:
    """HTML → readable plain text (keeps line breaks, drops script/style)."""
    if not raw:
        return ""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.DOTALL | re.IGNORECASE)
    text = _BR_RE.sub("\n", text)
    text = re.sub(r"</(p|div|li|h[1-6])>", "\n", text, flags=re.IGNORECASE)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    return "\n".join(line.strip() for line in text.split("\n") if line.strip())


def _fetch(url: str) -> str:
    with httpx.Client(timeout=FETCH_TIMEOUT, follow_redirects=True,
                      headers={"User-Agent": USER_AGENT}) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.text


# ── Scrapers (return a normalised item list) ───────────────────────────────
# item: {title, text, url, published_at (datetime|None), media[list], author}

def scrape_rss(url: str, limit: int = 20) -> tuple[str, list[dict[str, Any]]]:
    """Parse an RSS 2.0 or Atom feed with the stdlib (no extra dependency)."""
    raw = _fetch(url)
    root = ET.fromstring(raw.encode("utf-8", errors="ignore"))
    ns = {"atom": "http://www.w3.org/2005/Atom"}

    feed_title = ""
    items: list[dict[str, Any]] = []

    channel = root.find("channel")
    if channel is not None:  # RSS 2.0
        feed_title = (channel.findtext("title") or "").strip()
        entries = channel.findall("item")
        for entry in entries[:limit]:
            title = strip_html(entry.findtext("title") or "")
            body = strip_html(entry.findtext("description") or "")
            link = (entry.findtext("link") or "").strip()
            pub = entry.findtext("pubDate") or ""
            media = []
            enclosure = entry.find("enclosure")
            if enclosure is not None and enclosure.get("url"):
                mime = (enclosure.get("type") or "")
                kind = ("image" if mime.startswith("image") else
                        "audio" if mime.startswith("audio") else
                        "video" if mime.startswith("video") else "document")
                media.append({"kind": kind, "url": enclosure.get("url"), "name": title[:60]})
            items.append({"title": title, "text": body, "url": link,
                          "published_at": _parse_date(pub), "media": media, "author": feed_title})
    else:  # Atom
        feed_title = (root.findtext("atom:title", default="", namespaces=ns) or "").strip()
        for entry in root.findall("atom:entry", ns)[:limit]:
            title = strip_html(entry.findtext("atom:title", default="", namespaces=ns) or "")
            body = strip_html(
                entry.findtext("atom:content", default="", namespaces=ns)
                or entry.findtext("atom:summary", default="", namespaces=ns) or ""
            )
            link_el = entry.find("atom:link", ns)
            link = (link_el.get("href") if link_el is not None else "") or ""
            pub = entry.findtext("atom:updated", default="", namespaces=ns) or ""
            items.append({"title": title, "text": body, "url": link,
                          "published_at": _parse_date(pub), "media": [], "author": feed_title})

    return feed_title or url, items


def _parse_date(raw: str) -> Optional[datetime]:
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def scrape_web(url: str) -> tuple[str, list[dict[str, Any]]]:
    """Fetch one page and turn its readable text into a single post."""
    raw = _fetch(url)
    m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.DOTALL | re.IGNORECASE)
    title = strip_html(m.group(1)) if m else url
    body = strip_html(raw)[:8000]
    return title, [{"title": title, "text": body, "url": url,
                    "published_at": _now(), "media": [], "author": title}]


_TG_MSG_RE = re.compile(r'<div class="tgme_widget_message_wrap.*?</div>\s*</div>\s*</div>', re.DOTALL)
_TG_TEXT_RE = re.compile(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.DOTALL)
_TG_TIME_RE = re.compile(r'<time[^>]*datetime="([^"]+)"')
_TG_PHOTO_RE = re.compile(r"background-image:url\('([^']+)'\)")
_TG_VIEWS_RE = re.compile(r'<span class="tgme_widget_message_views">([^<]+)</span>')
_TG_TITLE_RE = re.compile(r'<div class="tgme_channel_info_header_title"[^>]*><span[^>]*>(.*?)</span>', re.DOTALL)


def scrape_tg_preview(ref: str, limit: int = 20) -> tuple[str, list[dict[str, Any]]]:
    """
    Read a PUBLIC Telegram channel through its anonymous web preview
    (``https://t.me/s/<channel>``). Read-only: this is the same page a browser
    shows without logging in — the simulation never authenticates anywhere.
    """
    ident = ref.strip()
    for prefix in ("https://t.me/s/", "http://t.me/s/", "https://t.me/", "http://t.me/", "t.me/"):
        if ident.lower().startswith(prefix):
            ident = ident[len(prefix):]
            break
    ident = ident.lstrip("@").strip("/")
    url = f"https://t.me/s/{ident}"
    raw = _fetch(url)

    title_m = _TG_TITLE_RE.search(raw)
    channel_title = strip_html(title_m.group(1)) if title_m else f"@{ident}"

    items: list[dict[str, Any]] = []
    for block in _TG_MSG_RE.findall(raw):
        text_m = _TG_TEXT_RE.search(block)
        text = strip_html(text_m.group(1)) if text_m else ""
        photos = [{"kind": "image", "url": u, "name": "photo"} for u in _TG_PHOTO_RE.findall(block)]
        if not text and not photos:
            continue
        time_m = _TG_TIME_RE.search(block)
        views_m = _TG_VIEWS_RE.search(block)
        items.append({
            "title": "", "text": text, "url": url,
            "published_at": _parse_date(time_m.group(1)) if time_m else None,
            "media": photos, "author": channel_title,
            "views": _parse_views(views_m.group(1)) if views_m else 0,
        })
    return channel_title, items[-limit:][::-1]


def _parse_views(raw: str) -> int:
    raw = (raw or "").strip().upper().replace(" ", "")
    try:
        if raw.endswith("K"):
            return int(float(raw[:-1]) * 1000)
        if raw.endswith("M"):
            return int(float(raw[:-1]) * 1_000_000)
        return int(float(raw))
    except ValueError:
        return 0


SCRAPERS = {"rss": scrape_rss, "web": scrape_web, "tg_preview": scrape_tg_preview}


# ── Materialising a scrape into the world ──────────────────────────────────

def ensure_channel(db: Session, world_id: int, username: str, title: str, *,
                   source: str = "landscape", external_ref: Optional[str] = None,
                   description: Optional[str] = None) -> SimChannel:
    """Get-or-create a sim channel by @username inside this world."""
    username = "@" + username.lstrip("@") if username else "@imported"
    existing = (
        db.query(SimChannel)
        .filter(SimChannel.world_id == world_id, SimChannel.username == username)
        .first()
    )
    if existing:
        return existing
    channel = SimChannel(
        world_id=world_id, username=username, title=title or username,
        description=description, source=source, external_ref=external_ref,
        avatar_color="teal" if source == "landscape" else "indigo",
    )
    db.add(channel)
    db.flush()
    return channel


def _slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", (text or "").strip().lower()).strip("_")
    return (s or "source")[:40]


def run_landscape_scrape(
    db: Session, world_id: int, kind: str, url: str, *,
    limit: int = 20, target_channel_id: Optional[int] = None,
    as_knowledge: bool = False, tags: Optional[list[str]] = None,
) -> dict[str, Any]:
    """
    Execute one scrape and materialise it inside the world.

    Returns a report ``{status, channel_id, posts, knowledge, title, message}``.
    Network/parse failures come back as ``status='error'`` with a message — the
    caller records that on the source row and in the activity feed.
    """
    scraper = SCRAPERS.get(kind)
    if scraper is None:
        return {"status": "error", "message": f"Неизвестный тип источника: {kind}"}

    try:
        title, items = scraper(url, limit) if kind != "web" else scraper(url)
    except httpx.HTTPStatusError as exc:
        return {"status": "error", "message": f"HTTP {exc.response.status_code} от источника."}
    except ET.ParseError as exc:
        return {"status": "error", "message": f"Не удалось разобрать фид: {exc}"}
    except Exception as exc:
        return {"status": "error", "message": f"Ошибка загрузки: {exc}"}

    if not items:
        return {"status": "error", "message": "Источник не вернул ни одной записи."}

    channel: Optional[SimChannel] = None
    if not as_knowledge:
        if target_channel_id:
            channel = db.get(SimChannel, target_channel_id)
        if channel is None:
            uname = _slug(title) if kind != "tg_preview" else _slug(url.split("/")[-1] or title)
            channel = ensure_channel(db, world_id, uname, title, external_ref=url,
                                     description=f"Импортировано из ландшафта ({kind}): {url}")

    posts_created = 0
    knowledge_created = 0
    base_time = _now()
    for i, item in enumerate(items[:limit]):
        body = "\n\n".join(p for p in [item.get("title") or "", item.get("text") or ""] if p).strip()
        if not body and not item.get("media"):
            continue
        if as_knowledge:
            db.add(SimKnowledge(
                world_id=world_id, kind="news", title=(item.get("title") or "")[:300],
                content=body[:6000], tags=list(tags or []), source=item.get("url") or url,
                origin=f"scrape:{kind}",
            ))
            knowledge_created += 1
            continue

        media = list(item.get("media") or [])
        if item.get("url") and kind != "tg_preview":
            media.append({"kind": "link", "url": item["url"], "name": (item.get("title") or item["url"])[:120]})
        published = item.get("published_at") or (base_time - timedelta(minutes=5 * (len(items) - i)))
        db.add(SimPost(
            channel_id=channel.id, text=body[:6000], media=media,
            reactions={}, views=int(item.get("views") or 0),
            author_label=None, source="landscape", external_ref=item.get("url") or url,
            published_at=published,
        ))
        posts_created += 1

    db.flush()
    return {
        "status": "ok", "title": title,
        "channel_id": channel.id if channel else None,
        "posts": posts_created, "knowledge": knowledge_created,
        "message": (f"Импортировано {posts_created} постов в «{title}»." if posts_created
                    else f"Импортировано {knowledge_created} записей знаний из «{title}»."),
    }


# ── Read-only imports from production ──────────────────────────────────────
# Every function below issues SELECTs against production tables and writes ONLY
# into sim_* rows. Nothing production-side is created, updated or deleted.

def import_knowledge_facts(db: Session, world_id: int, *, limit: int = 100,
                           layers: Optional[list[str]] = None,
                           query: Optional[str] = None) -> int:
    """Copy production KnowledgeFacts into the world's own RAG base."""
    from app.models import KnowledgeFact

    q = db.query(KnowledgeFact).order_by(KnowledgeFact.created_at.desc())
    if query:
        q = q.filter(KnowledgeFact.content.ilike(f"%{query}%"))
    rows = q.limit(max(1, min(limit, 500))).all()

    created = 0
    for fact in rows:
        fact_layers = list(fact.landscape_layers or [])
        if layers and not (set(layers) & set(fact_layers)):
            continue
        db.add(SimKnowledge(
            world_id=world_id, kind="fact", title=None, content=fact.content,
            tags=list(fact.tags or []) + list(fact.categories or []) + fact_layers,
            source=fact.source_url, origin="import:knowledge",
            weight=max(1, int(fact.source_count or 1)),
        ))
        created += 1
    db.flush()
    return created


def import_landscape_sources(db: Session, world_id: int) -> int:
    """Copy production scraping targets in as ready-to-run simulation sources."""
    from app.models import ScrapingLandscape
    from app.models_simulation import SimLandscapeSource

    rows = db.query(ScrapingLandscape).filter(ScrapingLandscape.is_active.is_(True)).all()
    existing = {
        s.url for s in db.query(SimLandscapeSource).filter(SimLandscapeSource.world_id == world_id).all()
    }
    created = 0
    for row in rows:
        ident = row.target_identifier
        kind = "tg_preview" if (row.platform == "telegram" or ident.startswith("@")) else \
               ("rss" if row.type in ("rss", "feed") else "web")
        if ident in existing:
            continue
        db.add(SimLandscapeSource(
            world_id=world_id, kind=kind, url=ident,
            title=f"{row.platform}: {ident}",
            options={"limit": 20, "tags": list(row.associated_tags or [])},
        ))
        created += 1
    db.flush()
    return created


def import_channel_profiles(db: Session, world_id: int, *, as_channels: bool = True) -> dict[str, int]:
    """
    Copy production ChannelProfiles in — as sim channels (so posts can hang off a
    realistic channel) and as ``channel_profile`` knowledge rows (so the prompt
    can be grounded on the channel's character).
    """
    from app.models import ChannelProfile

    rows = db.query(ChannelProfile).order_by(ChannelProfile.updated_at.desc()).limit(100).all()
    channels, knowledge = 0, 0
    for cp in rows:
        themes = [t.get("theme") for t in (cp.recent_themes or []) if isinstance(t, dict) and t.get("theme")]
        summary_bits = [
            f"Канал {cp.channel_ref} «{cp.title or ''}».",
            f"География: {cp.geo_label or ', '.join(cp.geo_layers or []) or '—'}.",
            f"Темы: {', '.join(cp.topics or []) or '—'}.",
        ]
        if themes:
            summary_bits.append("Сейчас обсуждают: " + ", ".join(themes) + ".")
        if cp.summary:
            summary_bits.append(cp.summary)
        db.add(SimKnowledge(
            world_id=world_id, kind="channel_profile", title=cp.title or cp.channel_ref,
            content=" ".join(summary_bits), tags=list(cp.topics or []) + list(cp.geo_layers or []),
            source=cp.channel_ref, origin="import:channel_profile",
        ))
        knowledge += 1
        if as_channels:
            username = cp.channel_ref if cp.channel_ref.startswith("@") else "@" + _slug(cp.channel_ref)
            ch = (
                db.query(SimChannel)
                .filter(SimChannel.world_id == world_id, SimChannel.username == username)
                .first()
            )
            if ch is None:
                db.add(SimChannel(
                    world_id=world_id, username=username, title=cp.title or cp.channel_ref,
                    description=cp.summary, geo_label=cp.geo_label,
                    tags=list(cp.topics or []), source="import", external_ref=cp.channel_ref,
                    avatar_color="grape",
                ))
                channels += 1
    db.flush()
    return {"channels": channels, "knowledge": knowledge}


def import_agent_profiles(db: Session, world_id: int, agent_ids: Optional[list[str]] = None) -> int:
    """
    Copy production souls in as EDITABLE simulation personas. The copy is a
    snapshot: editing it in the polygon never writes back to ``agent_profiles``.
    """
    from app.models import AgentProfile

    q = db.query(AgentProfile)
    if agent_ids:
        q = q.filter(AgentProfile.agent_id.in_(agent_ids))
    rows = q.all()

    existing = {
        p.agent_key for p in db.query(SimPersona).filter(SimPersona.world_id == world_id).all()
    }
    created = 0
    for profile in rows:
        key = profile.agent_id
        if key in existing:
            continue
        comm = profile.communication_style or {}
        db.add(SimPersona(
            world_id=world_id, agent_key=key, codename=profile.codename,
            full_name=profile.full_name, caste=profile.caste, status="active",
            bio=" ".join(filter(None, [
                profile.profession or "",
                f"из {profile.residence_city}" if profile.residence_city else "",
                profile.education or "",
            ])).strip() or None,
            core_mission=profile.core_mission,
            interests=list(profile.core_interests or []),
            style={
                "tone_level": comm.get("tone_level", 5),
                "vocab_level": comm.get("vocab_level", 5),
                "emoji_frequency": comm.get("emoji_frequency", 3),
                "aggression": comm.get("aggression", 3),
                "quirks": list(comm.get("quirks") or []),
                "language": (profile.spoken_languages or ["ru"])[0] if profile.spoken_languages else "ru",
            },
            settings={"behavioral_rules": profile.behavioral_rules or {},
                      "active_hours": [profile.active_hours_start, profile.active_hours_end]},
            source_agent_id=key,
        ))
        created += 1
    db.flush()
    return created


def import_activity_history(db: Session, world_id: int, limit: int = 100) -> int:
    """
    Copy the swarm's real past comments/replies (``agent_activity_logs``) in as
    ``history`` knowledge — material for a realistic polygon and a reference for
    "how did we actually talk about this before". Read-only on the source.
    """
    from app.models import AgentActivityLog

    rows = (
        db.query(AgentActivityLog)
        .filter(AgentActivityLog.text_content.isnot(None))
        .order_by(AgentActivityLog.created_at.desc())
        .limit(max(1, min(limit, 500)))
        .all()
    )
    created = 0
    for row in rows:
        text = (row.text_content or "").strip()
        if not text:
            continue
        db.add(SimKnowledge(
            world_id=world_id, kind="history",
            title=f"{row.agent_id} · {row.action_type}",
            content=text, tags=[row.platform, row.action_type],
            source=row.target_url, origin="import:history",
        ))
        created += 1
    db.flush()
    return created


def import_missions(db: Session, world_id: int, mission_ids: Optional[list[int]] = None) -> int:
    """
    Copy production mission definitions in as simulation-only scenarios (goal /
    stance / tactic). The copy has no link back: running it here can never touch
    the real mission, its targets or its squad.
    """
    from app.models import Mission

    q = db.query(Mission)
    if mission_ids:
        q = q.filter(Mission.id.in_(mission_ids))
    rows = q.order_by(Mission.id.desc()).limit(50).all()

    created = 0
    for m in rows:
        db.add(SimMission(
            world_id=world_id, title=f"{m.title} (копия)", goal=m.narrative_goal,
            stance=m.stance, worldview=m.forced_context, tactic=m.tactic or "dynamic",
            mode="comment", status="paused",
            scope={}, settings={"imported_from_production_mission_id": m.id},
        ))
        created += 1
    db.flush()
    return created
