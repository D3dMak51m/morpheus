"""
DAEDALUS — Tools: how the swarm finds out what it does not know (Stage 47)
===========================================================================
Until now the swarm could only know what happened to arrive through a fixed set of
news feeds. That is a hard ceiling, and it was measured rather than suspected:

  * recon on the transport mission found its own key words («пробки», «полоса»,
    «трафик») in **0 of 1594** facts — ten general news feeds will never cover one
    mission's specific subject;
  * anything that changes — a score, a price, today's news — is by definition absent
    from both the corpus and the model's training data, so a comment about it is
    invention.

So the swarm gets tools. Two, deliberately:

  ``search(query)``  — SearXNG over the compose network, JSON out.
  ``lookup(query)``  — search, then READ the pages behind the top results, then put
                       what was read through the ordinary knowledge pipeline
                       (scrub → junk gate → classify → embed → dedup) and return the
                       findings for immediate use.

`lookup` is the one callers want. Everything the swarm reads becomes part of what the
swarm knows — a search result used once and thrown away would leave the corpus exactly
as poor as before, which is the problem this exists to fix.

Design notes worth keeping:

  * The model is NOT asked to emit tool-call JSON. `qwen2.5:3b` cannot do it reliably
    (measured elsewhere in this codebase: asking it for JSON made it answer `false` to
    everything), so the decision to search is a one-word classification and the query
    is built from extracted terms. Tool USE is code; only the DECISION is model work.
  * Article extraction reuses `refetch.fetch_article_text` — the same trafilatura path
    the news pipeline uses, including its skip list for hosts whose pages extract to
    less than their feed (RT) or to nothing at all (daryo.uz).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

import httpx

logger = logging.getLogger("daedalus.tools")

SEARX_URL = os.getenv("SEARX_URL", "http://searxng:8080")
SEARCH_TIMEOUT = float(os.getenv("SEARCH_TIMEOUT_SEC", "20"))
# How many results a search returns, and how many of them are actually opened. Reading
# is the expensive half (a page fetch plus an LLM classification per article on
# ingest), so it stays well below the number of results.
SEARCH_RESULTS = int(os.getenv("SEARCH_RESULTS", "8"))
LOOKUP_READ = int(os.getenv("LOOKUP_READ_PAGES", "3"))
# A snippet is what goes into a prompt; the full article goes into the knowledge base.
SNIPPET_LEN = int(os.getenv("LOOKUP_SNIPPET_LEN", "400"))

# Hosts that are never worth opening: aggregators, video and social pages carry no
# article text, and a lookup that spends its reading budget on them returns nothing.
SKIP_HOSTS = (
    "youtube.com", "youtu.be", "tiktok.com", "instagram.com", "facebook.com",
    "twitter.com", "x.com", "pinterest.", "amazon.", "aliexpress.",
    # App stores and map pages rank high for exactly the words a transport mission
    # searches. Measured: the first recon search filed a Google Play listing for the
    # Yandex Metro app as a fact about public transport.
    "play.google.com", "apps.apple.com", "yandex.ru/maps", "yandex.com/maps",
    "google.com/maps", "2gis.", "wikipedia.org/wiki/Special",
)


def available() -> bool:
    """Is the search back-end reachable? (Shown to the operator; never raises.)"""
    try:
        r = httpx.get(f"{SEARX_URL}/healthz", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def search(query: str, limit: int = SEARCH_RESULTS, language: str = "ru",
           recent: bool = False) -> list[dict[str, Any]]:
    """
    Web results for a query: ``[{title, url, snippet, engine, published}]``.

    ``recent`` restricts to the last month — the right default when the question is
    about something that changes, and the wrong one when it is about background.
    Returns [] on any failure: a swarm that cannot search must fall back to what it
    already knows, not stall.
    """
    q = " ".join((query or "").split())[:300]
    if not q:
        return []
    params = {"q": q, "format": "json", "language": language, "safesearch": 0}
    if recent:
        params["time_range"] = "month"
    try:
        r = httpx.get(f"{SEARX_URL}/search", params=params, timeout=SEARCH_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        logger.warning("tools.search failed for %r: %s", q[:60], exc)
        return []
    out = []
    for row in (data.get("results") or [])[: max(limit * 2, limit)]:
        url = (row.get("url") or "").strip()
        if not url or any(h in url.lower() for h in SKIP_HOSTS):
            continue
        out.append({
            "title": " ".join((row.get("title") or "").split())[:200],
            "url": url,
            "snippet": " ".join((row.get("content") or "").split())[:400],
            "engine": row.get("engine") or "",
            "published": row.get("publishedDate") or None,
        })
        if len(out) >= limit:
            break
    logger.info("tools.search %r → %d result(s)", q[:60], len(out))
    return out


def _ingest(db, content: str, source_url: str, layers: list[str]) -> Optional[int]:
    """Put one read article through the ordinary knowledge pipeline."""
    # Imported here: router_knowledge imports heavy siblings, and tools.py is also
    # imported by the recon path.
    from app.router_knowledge import (clean_fact_text, is_junk_fact, _resolve_embedding,
                                      _upsert_fact)
    from app.classifier import auto_classify_text
    import asyncio

    cleaned = clean_fact_text(content)
    if not cleaned or is_junk_fact(cleaned):
        return None
    # Callers are sync (a recon run, an internal endpoint declared `def`), so FastAPI
    # runs them in a worker thread with no event loop of its own — `asyncio.run` is
    # correct here. A failed classification is not a reason to drop the article: an
    # unclassified fact still carries its text, and layers fall back to the caller's.
    try:
        classification = asyncio.run(auto_classify_text(cleaned))
    except Exception as exc:
        logger.debug("tools: classification failed (%s) — filing unclassified", exc)
        classification = {"layers": [], "categories": [], "tags": [], "geo_tags": []}
    merged = list({*(classification.get("layers") or []), *layers}) or ["global"]
    try:
        embedding = _resolve_embedding(None, cleaned)
        _action, fact, _sim = _upsert_fact(
            db, cleaned, source_url, merged,
            classification.get("categories") or [], classification.get("tags") or [],
            classification.get("geo_tags") or [], embedding, None,
        )
        return fact.id
    except Exception as exc:
        logger.warning("tools: could not file %s: %s", source_url[:60], exc)
        db.rollback()
        return None


def lookup(db, query: str, layers: Optional[list[str]] = None,
           read_pages: int = LOOKUP_READ, recent: bool = True,
           language: str = "ru") -> dict[str, Any]:
    """
    Find out about ``query``: search, read the top pages, file what was read, and
    return the findings.

    Returns ``{query, results, findings, filed}`` where ``findings`` are compact
    ``{title, url, text}`` entries a prompt can carry, and ``filed`` counts what
    entered the knowledge base. A lookup that finds nothing returns empty lists — the
    honest answer, and the caller must be able to say "we don't know" rather than
    invent.
    """
    from app.refetch import should_skip

    results = search(query, language=language, recent=recent)
    findings: list[dict[str, str]] = []
    filed = 0
    for row in results:
        if len(findings) >= read_pages:
            break
        url = row["url"]
        if should_skip(url):
            # Hosts whose pages are known to extract to nothing (daryo.uz) or to less
            # than their own feed (RT) — the snippet is the better text there.
            if row["snippet"]:
                findings.append({"title": row["title"], "url": url, "text": row["snippet"]})
            continue
        text = _read_page(url)
        if not text:
            if row["snippet"]:
                findings.append({"title": row["title"], "url": url, "text": row["snippet"]})
            continue
        body = f"{row['title']}. {text}" if row["title"] else text
        if _ingest(db, body, url, list(layers or ["global"])):
            filed += 1
        findings.append({"title": row["title"], "url": url,
                         "text": " ".join(text.split())[:SNIPPET_LEN]})
    logger.info("tools.lookup %r → %d finding(s), %d filed", query[:60], len(findings), filed)
    return {"query": query, "results": len(results), "findings": findings, "filed": filed}


def _read_page(url: str) -> str:
    """The article behind a result, or "" — never raises."""
    try:
        from app.refetch import fetch_article_text
        return (fetch_article_text(url) or "").strip()
    except Exception as exc:
        logger.debug("tools: could not read %s: %s", url[:60], exc)
        return ""


# ── Turning a mission / a discussion into a query ─────────────────────────

_QUOTE_RE = re.compile(r"[«»\"'`]+")


def clean_query(text: str, limit: int = 120) -> str:
    """A search query out of free text: no quotes, no newlines, bounded."""
    q = _QUOTE_RE.sub(" ", " ".join((text or "").split()))
    return q[:limit].strip()
