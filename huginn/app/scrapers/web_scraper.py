"""
HUGINN — Web Scraper
======================
Scrapes non-RSS web sources into the knowledge base, using curl_cffi for
browser-level TLS/JA4 fingerprint emulation.

Stage 39 — it now reads the ARTICLE, not the link.

The previous version fetched a source's front page, collected `<a>` texts and
ingested those strings as facts. It never opened an article. On CNN that produced a
knowledge base of photo credits and teasers — "Win McNamee/Getty Images", "Charli XCX
chasing cool", "…after its signingShow all" — 83 such records were purged from the
live corpus, and every one of them had also been embedded and offered to the bots as
world knowledge.

Now: front page → article links → fetch each article → extract its body text
(trafilatura) → ingest. Anything that does not yield a real body is dropped rather
than degraded into a headline, because a headline is exactly the junk we removed.
"""

import asyncio
import logging
import re
import os
import time
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession

from app.article_fetcher import extract_article
from app.knowledge_ingest import capture_event, ingest_knowledge, report_scrape

logger = logging.getLogger("huginn.scrapers.web_scraper")

# Articles fetched per source per pass. Each costs a request plus an LLM
# classification downstream, so this stays deliberately small.
MAX_ARTICLES_PER_PASS = int(os.getenv("WEB_MAX_ARTICLES", "5"))
# Below this an extraction is a nav blurb or a paywall stub, not an article.
# How much of the body to keep. The ingest endpoint truncates too; this bounds the
# payload and keeps one long read from dominating an embedding.
POLL_INTERVAL_SEC = int(os.getenv("WEB_POLL_INTERVAL", "300"))

# URL path fragments that are never articles.
_NON_ARTICLE_HINTS = (
    "/tag/", "/tags/", "/category/", "/categories/", "/author/", "/live/",
    "/video/", "/videos/", "/gallery/", "/photo/", "/search", "/about",
    "/contact", "/subscribe", "/privacy", "/terms", "/rss", "/feed",
    "/audio/", "/podcasts/", "/profiles/", "/interactive/", "/specials/",
    "javascript:", "mailto:", "#",
)

# "/2026/08/11/…" — the date path almost every news CMS puts on an article.
_YEAR_SEGMENT_RE = re.compile(r"/(19|20)\d{2}/")


def _looks_like_article(url: str, base_host: str) -> bool:
    """
    Cheap filter for links worth opening.

    Requires a same-host link whose path carries a DATE segment (`/2026/08/11/…`) —
    the mark of a story published on a day, as opposed to a standing section.

    A hyphenated-slug fallback was tried first and measured too weak: CNN names its
    hubs the same way, so `/sport/milan-cortina-winter-olympics-2026` and
    `/audio/podcasts/all-there-is-with-anderson-cooper` passed and were stored as
    "facts" that are really tables of contents. Sites without dated URLs are served by
    the RSS path instead; if one has neither, it reports zero links and shows up as
    `degraded` in the landscape rather than quietly filling the base with hub pages.
    """
    if not url or url.startswith(("javascript:", "mailto:", "#")):
        return False
    parsed = urlparse(url)
    if parsed.netloc and base_host not in parsed.netloc:
        return False        # off-site link (ads, social)
    path = (parsed.path or "").lower()
    if any(hint in path for hint in _NON_ARTICLE_HINTS):
        return False
    if len([p for p in path.split("/") if p]) < 2:
        return False
    return bool(_YEAR_SEGMENT_RE.search(path))


def _collect_links(html: str, page_url: str) -> List[str]:
    """Absolute, de-duplicated candidate article URLs from an index page."""
    soup = BeautifulSoup(html, "html.parser")
    base_host = urlparse(page_url).netloc
    seen: List[str] = []
    for a_tag in soup.find_all("a", href=True):
        href = urljoin(page_url, a_tag["href"].strip())
        if _looks_like_article(href, base_host) and href not in seen:
            seen.append(href)
    return seen


async def run_web_scraper(redis_client, raw_events_queue, is_content_expired_func,
                          publish_func, active_targets: Dict[str, List[Any]]):
    """Poll each configured web source, open its new articles, ingest their text."""
    logger.info("Web scraper started (article-body extraction).")

    async with AsyncSession(impersonate="chrome") as session:
        while True:
            try:
                for url_item in active_targets.get("web", []):
                    if isinstance(url_item, dict):
                        url = url_item.get("target_identifier")
                        target_layers = url_item.get("default_layers", ["global"])
                    else:
                        url, target_layers = url_item, ["global"]
                    if not url:
                        continue

                    try:
                        response = await session.get(url, timeout=20)
                    except Exception as exc:
                        logger.warning("Failed to fetch %s: %s", url, exc)
                        report_scrape(url, parsed=0, error=str(exc)[:300])
                        continue

                    if response.status_code != 200:
                        logger.warning("Failed to fetch %s: HTTP %s", url, response.status_code)
                        report_scrape(url, parsed=0, error=f"HTTP {response.status_code}")
                        continue

                    links = _collect_links(response.text, url)
                    if not links:
                        logger.warning("Web source %s yielded no article links — layout change?", url)

                    ingested, attempted = 0, 0
                    for link in links:
                        if ingested >= MAX_ARTICLES_PER_PASS:
                            break
                        post_id = str(uuid.uuid5(uuid.NAMESPACE_URL, link))
                        cache_key = f"cache:web:{post_id}"
                        if redis_client.get(cache_key):
                            continue

                        try:
                            art_resp = await session.get(link, timeout=20)
                        except Exception as exc:
                            logger.debug("Article fetch failed %s: %s", link, exc)
                            continue
                        if art_resp.status_code != 200:
                            continue

                        attempted += 1
                        article = extract_article(art_resp.text, link)
                        # Cache regardless: a page with no extractable article will not
                        # grow one, and re-fetching it every 5 minutes is pure waste.
                        redis_client.setex(cache_key, 86400, "1")
                        if not article:
                            continue

                        if is_content_expired_func("web", int(time.time())):
                            continue

                        text = article["body"]
                        if article["title"]:
                            text = f"{article['title']}. {text}"
                        # Stage 22 — generic web news routes ONLY to the knowledge base,
                        # never to the execution queue / News Hub.
                        if not ingest_knowledge(text=text, source_url=link,
                                                default_layers=target_layers,
                                                published_at=article.get("published_at")):
                            continue
                        capture_event(text=text, source_platform="web", source_target=url,
                                      link=link, default_layers=target_layers)
                        ingested += 1
                        await asyncio.sleep(1)   # be polite to the source

                    # A source can offer plenty of links and still yield nothing usable:
                    # daryo.uz does not put article bodies in its HTML, so every
                    # extraction there is the page's nav and comment widget. Left
                    # unreported that looks identical to "no new articles".
                    extraction_error = None
                    if attempted and not ingested:
                        extraction_error = (
                            f"Открыто статей: {attempted}, принято: 0 — тело статьи не "
                            f"извлекается (сайт отдаёт только навигацию/интерфейс)."
                        )
                    report_scrape(url, parsed=len(links), ingested=ingested,
                                  error=extraction_error)
                    if ingested:
                        logger.info("Web: ingested %d article(s) from %s", ingested, url)

            except Exception as exc:
                logger.error("Error in Web Scraper loop: %s", exc)

            await asyncio.sleep(POLL_INTERVAL_SEC)
