"""
HUGINN — RSS Scraper (Stage 22)
=================================
Polls RSS/Atom feeds and routes every entry into MUNINN's knowledge base
(`/api/v1/knowledge/internal/ingest`), mirroring it into the operator's News Hub for
visibility. It NEVER touches the execution queue — RSS is a pure epistemology source
and nothing read here is ever commented on.

Feeds are taken from the dynamic landscape (platform == "rss"); each target may
carry `default_layers`. A small static fallback list keeps the scraper useful
before any RSS targets are configured.

`feedparser.parse` is blocking, so it is run in a thread to avoid stalling the
asyncio event loop. Runnable standalone (`python -m app.scrapers.rss_scraper`) for a
quick feed sanity check.

Stage 39 — this module used to live at the repo root as `test_rss.py`, i.e. the
production RSS scraper was shipped as a file named like a test. It also had a shadow:
`social_feed_scraper` polled the SAME feeds on its own schedule and pushed them to
`queue:raw_events`, which nothing consumes. Both are fixed; feeds are fetched once,
here, and every pass reports source health.
"""

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

import feedparser
import httpx

from app.article_fetcher import fetch_article, better_text
from app.knowledge_ingest import capture_event, ingest_knowledge, report_scrape

logger = logging.getLogger("huginn.scrapers.rss_scraper")

# Fallback feeds used only when no platform=="rss" targets are configured.
FALLBACK_FEEDS = [
    "https://feeds.bbci.co.uk/news/rss.xml",
    "https://russian.rt.com/rss",
    "https://centrasia.org/rss/redtram.xml",
    "https://khovar.tj/rus/feed/",
]

RSS_POLL_INTERVAL_SEC = int(os.getenv("RSS_POLL_INTERVAL", "600"))  # 10 min
# Entries processed per feed per pass. Each costs an article fetch plus an LLM
# classification downstream, so the cadence stays modest; entries are deduped, so this
# is only ever spent on genuinely new items.
#
# This is BOTH the slice of the feed we walk and the article-fetch budget, deliberately.
# They used to differ (walk 15, fetch 12) and the mismatch was silently destructive:
# entries 13-15 were stored as bare teasers AND stamped with the 24h dedup key, so they
# could never be retried. Measured on BBC — 97 of 107 stored facts were stubs while the
# same articles extract to 4578 characters on demand. Anything past the budget is now
# simply left for the next pass, unstamped.
MAX_ARTICLE_FETCH = int(os.getenv("RSS_MAX_ARTICLE_FETCH", "15"))
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")


def _parse_feed(url: str):
    """Blocking feedparser call (executed in a thread)."""
    return feedparser.parse(url)


async def run_rss_scraper(redis_client, active_targets: Dict[str, List[Any]]):
    """
    Async loop: poll each configured RSS feed, dedup via Redis, open each new entry's
    article for its full text, and ingest into MUNINN. No execution-queue writes.
    """
    logger.info("RSS scraper started (knowledge-only, full-article extraction).")

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True,
                                 headers={"User-Agent": _UA}) as http:
        while True:
            try:
                targets = active_targets.get("rss", [])
                if not targets:
                    targets = [{"target_identifier": u, "default_layers": ["global"]}
                               for u in FALLBACK_FEEDS]

                for item in targets:
                    if isinstance(item, dict):
                        feed_url = item.get("target_identifier")
                        feed_layers = item.get("default_layers", ["global"])
                    else:
                        feed_url, feed_layers = item, ["global"]
                    if not feed_url:
                        continue

                    try:
                        parsed = await asyncio.to_thread(_parse_feed, feed_url)
                    except Exception as exc:
                        logger.warning("Failed to parse RSS feed %s: %s", feed_url, exc)
                        report_scrape(feed_url, parsed=0, error=str(exc)[:300])
                        continue

                    # Stage 39 — an empty parse is a health event, not silence.
                    # `kun.uz/ru/news/rss` answered HTTP 200 with an HTML page: 0
                    # entries, no warning, and the Uzbek half of the base stayed empty.
                    if not parsed.entries:
                        logger.warning(
                            "RSS feed %s parsed 0 entries (status=%s, bozo=%s) — not a feed?",
                            feed_url, getattr(parsed, "status", None),
                            getattr(parsed, "bozo", None),
                        )

                    count = 0
                    fetched = 0
                    for entry in parsed.entries:
                        # Stop at the budget rather than degrading the remainder: an
                        # entry we cannot open properly this pass stays uncached and is
                        # picked up whole on the next one.
                        if fetched >= MAX_ARTICLE_FETCH:
                            break
                        link = entry.get("link") or entry.get("id") or ""
                        title = (entry.get("title") or "").strip()
                        summary = (entry.get("summary") or "").strip()
                        if not title and not summary:
                            continue

                        # Dedup by entry link (24h TTL cache).
                        dedup_key = f"cache:rss:{link or title}"
                        if redis_client.get(dedup_key):
                            continue
                        redis_client.setex(dedup_key, 86400, "1")

                        # The feed's own publication date, so a story is aged by when
                        # it was published rather than when this loop happened to see it.
                        stamp = entry.get("published_parsed") or entry.get("updated_parsed")
                        published_at = None
                        if stamp:
                            try:
                                published_at = datetime.fromtimestamp(
                                    time.mktime(stamp), tz=timezone.utc).isoformat()
                            except Exception:
                                published_at = None

                        # A feed entry is an ANNOUNCEMENT. Open the article itself and
                        # keep whichever text is actually richer — measured, the page
                        # beats the feed by 4-44x nearly everywhere, but RT extracts to
                        # LESS than its own summary, so this compares rather than replaces.
                        feed_text = f"{title}. {summary}".strip(". ").strip()
                        article = None
                        if link:
                            fetched += 1                    # budget enforced at loop top
                            article = await fetch_article(link, http)
                            await asyncio.sleep(0.5)        # be polite to the source
                        text, article_published = better_text(feed_text, article)
                        published_at = published_at or article_published

                        if not ingest_knowledge(text=text, source_url=link or None,
                                                default_layers=feed_layers,
                                                published_at=published_at):
                            continue
                        # Mirror into the News Hub for the operator's live view.
                        capture_event(text=text, source_platform="rss",
                                      source_target=feed_url, link=link or None,
                                      default_layers=feed_layers)
                        count += 1

                    report_scrape(feed_url, parsed=len(parsed.entries), ingested=count)
                    if count:
                        logger.info("RSS: ingested %d new entries from %s (%d article(s) opened)",
                                    count, feed_url, fetched)

            except Exception as exc:
                logger.error("Error in RSS scraper loop: %s", exc)

            await asyncio.sleep(RSS_POLL_INTERVAL_SEC)


if __name__ == "__main__":
      # Standalone feed sanity check (no ingestion).
      for f in FALLBACK_FEEDS:
          d = feedparser.parse(f)
          print(f"Feed: {f}\nEntries: {len(d.entries)}")
          if d.entries:
              print("First:", d.entries[0].title)
          print("-" * 20)
