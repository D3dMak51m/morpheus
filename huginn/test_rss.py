"""
HUGINN — RSS Scraper (Stage 22)
=================================
Polls RSS/Atom feeds and routes every entry EXCLUSIVELY into MUNINN's knowledge
base (`/api/v1/knowledge/internal/ingest`). It NEVER touches the execution queue
or the News Hub — RSS is a pure epistemology source.

Feeds are taken from the dynamic landscape (platform == "rss"); each target may
carry `default_layers`. A small static fallback list keeps the scraper useful
before any RSS targets are configured.

`feedparser.parse` is blocking, so it is run in a thread to avoid stalling the
asyncio event loop. Runnable standalone (`python test_rss.py`) for a quick feed
sanity check.
"""

import asyncio
import logging
import os
import time
from typing import Any, Dict, List

import feedparser

from app.knowledge_ingest import ingest_knowledge

logger = logging.getLogger("huginn.scrapers.rss_scraper")

# Fallback feeds used only when no platform=="rss" targets are configured.
FALLBACK_FEEDS = [
    "https://feeds.bbci.co.uk/news/rss.xml",
    "https://russian.rt.com/rss",
    "https://centrasia.org/rss/redtram.xml",
    "https://khovar.tj/rus/feed/",
]

RSS_POLL_INTERVAL_SEC = int(os.getenv("RSS_POLL_INTERVAL", "600"))  # 10 min


def _parse_feed(url: str):
    """Blocking feedparser call (executed in a thread)."""
    return feedparser.parse(url)


async def run_rss_scraper(redis_client, active_targets: Dict[str, List[Any]]):
    """
    Async loop: poll each configured RSS feed, dedup via Redis, and ingest each
    new entry into MUNINN. Pure knowledge routing — no execution-queue writes.
    """
    logger.info("RSS scraper started (knowledge-only).")

    while True:
        try:
            targets = active_targets.get("rss", [])
            if not targets:
                targets = [{"target_identifier": u, "default_layers": ["global"]} for u in FALLBACK_FEEDS]

            for item in targets:
                if isinstance(item, dict):
                    feed_url = item.get("target_identifier")
                    feed_layers = item.get("default_layers", ["global"])
                else:
                    feed_url = item
                    feed_layers = ["global"]
                if not feed_url:
                    continue

                try:
                    parsed = await asyncio.to_thread(_parse_feed, feed_url)
                except Exception as exc:
                    logger.warning("Failed to parse RSS feed %s: %s", feed_url, exc)
                    continue

                count = 0
                for entry in parsed.entries[:15]:
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

                    # Title + summary gives the classifier/embedder richer signal.
                    text = f"{title}. {summary}".strip(". ").strip()
                    ingest_knowledge(text=text, source_url=link or None, default_layers=feed_layers)
                    count += 1

                if count:
                    logger.info("RSS: ingested %d new entries from %s", count, feed_url)

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
