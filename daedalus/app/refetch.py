"""
DAEDALUS — Backfill of truncated facts (Stage 39)
===================================================
One-off repair for facts stored as RSS teasers before the scrapers learned to open
the article itself.

Those entries have since rotated out of their feeds, so the scrapers will never see
them again and the stubs cannot self-heal — but every fact keeps its ``source_url``,
which is enough to go and read the article now. Measured on the live corpus, 253 facts
are in this state (BBC, foxnews, khovar, older kun.uz), each holding roughly 200-350
characters where the article carries 1300-7800.

Two groups are deliberately excluded, because for them the stub is the CORRECT text:

  * ``russian.rt.com`` — its article pages extract to LESS than its own feed summary
    (measured 349 vs 379 characters), so refetching would downgrade 477 facts;
  * ``daryo.uz`` — the site does not expose article bodies in its HTML at all; every
    extraction is the comment widget and a subscription ad, which is why the source is
    already flagged ``degraded``.
"""

import logging
import os
from typing import Optional

import httpx
import trafilatura

logger = logging.getLogger("daedalus.refetch")

FETCH_TIMEOUT = float(os.getenv("REFETCH_TIMEOUT", "20"))
# Hosts whose article pages are known not to improve on what we already store.
DEFAULT_SKIP_HOSTS = tuple(
    h.strip() for h in os.getenv("REFETCH_SKIP_HOSTS", "russian.rt.com,daryo.uz").split(",")
    if h.strip()
)

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")


def should_skip(url: Optional[str], skip_hosts: tuple[str, ...] = DEFAULT_SKIP_HOSTS) -> bool:
    """True when this source is known to be worse (or empty) when fetched directly."""
    if not url:
        return True
    lowered = url.lower()
    return any(host in lowered for host in skip_hosts)


def fetch_article_text(url: str) -> Optional[str]:
    """
    Fetch ``url`` and return the article body, or None if there is no real article.

    Same extractor as HUGINN uses at scrape time, so a backfilled fact is
    indistinguishable from one ingested through the normal path.
    """
    try:
        with httpx.Client(timeout=FETCH_TIMEOUT, follow_redirects=True,
                          headers={"User-Agent": _UA}) as client:
            resp = client.get(url)
        if resp.status_code != 200:
            return None
        body = trafilatura.extract(resp.text, url=url, include_comments=False,
                                   include_tables=False, no_fallback=False)
    except Exception as exc:
        logger.debug("Refetch failed for %s: %s", url, exc)
        return None
    if not body:
        return None
    body = " ".join(body.split())

    title = ""
    try:
        meta = trafilatura.extract_metadata(resp.text)
        title = (getattr(meta, "title", "") or "").strip()
    except Exception:
        pass
    if title and not body[:len(title) + 8].lower().startswith(title[:40].lower()):
        body = f"{title}. {body}"
    return body
