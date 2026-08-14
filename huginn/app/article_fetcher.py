"""
HUGINN — Article body extraction (Stage 39)
=============================================
Shared by the RSS and web scrapers: given a URL, fetch the page and pull out the
article's own text, discarding navigation, comment widgets and promo blocks.

Why RSS needs this at all
-------------------------
An RSS entry is an ANNOUNCEMENT, not the news. Measured across the configured feeds,
the article page carries many times what the feed's `summary` does:

    BBC       196 ch → 4578 ch   (×23)      podrobno.uz   41 ch → 1786 ch  (×44)
    kun.uz    357 ch → 6595 ch   (×18)      foxnews      241 ch → 3170 ch  (×13)
    gazeta.uz 424 ch → 7775 ch   (×18)      uza.uz       303 ch → 1675 ch  (×5.5)
    khovar    331 ch → 1300 ch   (×3.9)     RT           379 ch →  349 ch  (×0.9)

So storing the feed text meant the swarm "knew" only headlines — which is exactly why
Telegram posts (full text by nature) read well while every RSS-sourced fact was a stub.

RT is the counter-example and the reason this is a comparison, not a replacement: its
article pages yield LESS than its feed. `better_text` therefore keeps whichever
version is actually richer, per item, rather than assuming the page always wins.
"""

import logging
import os
from typing import Optional

import httpx
import trafilatura

logger = logging.getLogger("huginn.article_fetcher")

# Below this an extraction is a nav blurb or a paywall stub, not an article.
MIN_ARTICLE_CHARS = int(os.getenv("WEB_MIN_ARTICLE_CHARS", "300"))
# Upper bound on what we ship to DAEDALUS; it truncates again on a sentence boundary.
MAX_ARTICLE_CHARS = int(os.getenv("WEB_MAX_ARTICLE_CHARS", "8000"))
ARTICLE_FETCH_TIMEOUT = float(os.getenv("ARTICLE_FETCH_TIMEOUT", "20"))
# The page must beat the feed by this factor to be worth preferring — a marginally
# longer extraction is usually the same lede plus a share widget.
BETTER_TEXT_RATIO = float(os.getenv("ARTICLE_BETTER_RATIO", "1.2"))

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")


def extract_article(html: str, url: str) -> Optional[dict]:
    """
    Pull an article's title, body and publication date out of a page.

    trafilatura finds the body and drops nav/promo/comment chrome, which naive
    `<a>`-text or `summary` scraping never could. Returns None when the page holds no
    real article (daryo.uz, for instance, does not expose article text in its HTML at
    all — every extraction there is the comment widget and a subscription ad).
    """
    try:
        body = trafilatura.extract(html, url=url, include_comments=False,
                                   include_tables=False, no_fallback=False)
    except Exception as exc:
        logger.debug("trafilatura failed on %s: %s", url, exc)
        return None
    if not body:
        return None
    body = " ".join(body.split())
    if len(body) < MIN_ARTICLE_CHARS:
        return None

    title, published = "", None
    try:
        meta = trafilatura.extract_metadata(html)
        title = (getattr(meta, "title", "") or "").strip()
        raw_date = (getattr(meta, "date", "") or "").strip()
        if raw_date:
            published = f"{raw_date}T00:00:00+00:00" if len(raw_date) == 10 else raw_date
    except Exception:
        pass
    return {"title": title, "body": body[:MAX_ARTICLE_CHARS], "published_at": published}


async def fetch_article(url: str, client: Optional[httpx.AsyncClient] = None) -> Optional[dict]:
    """Fetch ``url`` and extract its article. None on any failure — never raises."""
    if not url:
        return None
    own_client = client is None
    try:
        if own_client:
            client = httpx.AsyncClient(timeout=ARTICLE_FETCH_TIMEOUT,
                                       follow_redirects=True, headers={"User-Agent": _UA})
        resp = await client.get(url)
        if resp.status_code != 200:
            return None
        return extract_article(resp.text, url)
    except Exception as exc:
        logger.debug("Article fetch failed for %s: %s", url, exc)
        return None
    finally:
        if own_client and client is not None:
            await client.aclose()


def better_text(feed_text: str, article: Optional[dict]) -> tuple[str, Optional[str]]:
    """
    Choose between the feed's own text and the fetched article body.

    Returns ``(text, published_at)``. The article wins only when it is materially
    richer — RT's pages extract to less than its feed, and silently downgrading those
    would trade a usable summary for a worse one.
    """
    feed_text = (feed_text or "").strip()
    if not article:
        return feed_text, None
    body = (article.get("body") or "").strip()
    if not body:
        return feed_text, article.get("published_at")
    if len(body) < len(feed_text) * BETTER_TEXT_RATIO:
        return feed_text, article.get("published_at")
    title = (article.get("title") or "").strip()
    # Prefix the title only when the body does not already open with it.
    if title and not body[:len(title) + 8].lower().startswith(title[:40].lower()):
        body = f"{title}. {body}"
    return body, article.get("published_at")
