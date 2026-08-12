"""
HUGINN — Knowledge Ingestion (Stage 22)
=========================================
Generic scrapers (RSS / Web / general Telegram) route news into MUNINN's clustered
semantic memory, and mirror it into the operator's News Hub for visibility. Neither
path can cause the swarm to act: nothing here reaches the execution queue.

HUGINN now sends only raw text + the source's ``default_layers`` to DAEDALUS
``/api/v1/knowledge/internal/ingest``. DAEDALUS owns the heavy cognitive work:
LLM auto-classification (layers/categories/tags) → vector embedding → pgvector
cosine dedup. Keeping embedding+classification centralised guarantees one
consistent model and avoids HUGINN holding GPU state.

Fail-soft: any network error logs a warning and is dropped, never blocking the
scraper loops.
"""

import logging
import os
import time
import uuid
from typing import Optional

import httpx

logger = logging.getLogger("huginn.knowledge_ingest")

DAEDALUS_URL = os.getenv("DAEDALUS_URL", "http://daedalus:8000")
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "morpheus-internal-sync-key")


def ingest_knowledge(text: str, source_url: Optional[str], default_layers=None,
                     published_at: Optional[str] = None) -> bool:
    """
    Push raw scraped text to DAEDALUS for classification + clustering into MUNINN.

    ``default_layers`` is the list of landscape layers configured on the source;
    DAEDALUS merges them with the LLM-extracted layers. ``published_at`` is the
    source's own publication timestamp (ISO 8601) so freshness reflects the story's
    age rather than when we happened to scrape it. Fail-soft.

    Returns True when DAEDALUS stored the text, False when it was rejected as
    boilerplate or the call failed — callers use this to avoid mirroring junk into the
    News Hub and to notice a source whose articles never extract.
    """
    text = (text or "").strip()
    if not text:
        return False
    if not default_layers:
        default_layers = ["global"]

    try:
        with httpx.Client(timeout=90.0) as client:
            resp = client.post(
                f"{DAEDALUS_URL}/api/v1/knowledge/internal/ingest",
                json={
                    "content": text[:8000],
                    "source_url": source_url,
                    "default_layers": default_layers,
                    "published_at": published_at,
                },
                headers={"X-Internal-Token": INTERNAL_API_TOKEN},
            )
            # 422 = DAEDALUS judged the text to be site boilerplate rather than news.
            # That is an expected outcome, not an error: report it, do not shout.
            if resp.status_code == 422:
                logger.info("Knowledge ingest REJECTED as boilerplate: %s", (source_url or "")[:90])
                return False
            resp.raise_for_status()
            data = resp.json()
        logger.info(
            "Knowledge ingest: %s fact #%s (layers=%s, cluster_size=%s).",
            data.get("action"), data.get("fact_id"),
            data.get("landscape_layers"), data.get("source_count"),
        )
        return True
    except Exception as exc:
        logger.warning("Failed to ingest knowledge into DAEDALUS: %s", exc)
        return False


def capture_event(text: str, source_platform: str, source_target: str,
                  link: Optional[str] = None, default_layers=None) -> None:
    """
    Mirror a scraped item into the operator's News Hub (``captured_raw_events``).

    Stage 39 — observability ONLY. The previous wiring pushed each scraped item to
    `queue:raw_events` as well, where ORPHEUS wrote a comment for it and queued an
    execution task whose `target_url` was the item's `source_target` — i.e. the RSS
    feed's own URL (`https://russian.rt.com/rss`). That is not a channel anyone can
    post to, so the branch burned GPU generating comments addressed to a feed. This
    function deliberately writes the display row and nothing else: no Redis queue, no
    generation, no execution.

    `event_id` is derived from the link so re-scraping the same item is idempotent
    (the endpoint skips an event_id it already holds). Fail-soft.
    """
    text = (text or "").strip()
    if not text:
        return
    ref = link or f"{source_target}:{text[:80]}"
    event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, ref))
    try:
        with httpx.Client(timeout=10.0) as client:
            client.post(
                f"{DAEDALUS_URL}/api/v1/huginn/internal/capture",
                json={
                    "event_id": event_id,
                    "source_platform": source_platform,
                    "source_target": source_target,
                    "post_id": link or event_id,
                    "text_content": text[:8000],
                    "media_type": None,
                    "media_path": None,
                    "layers": {"global": True, "landscape": list(default_layers or ["global"])},
                    "timestamp": int(time.time()),
                },
                headers={"X-Internal-Token": INTERNAL_API_TOKEN},
            )
    except Exception as exc:
        logger.debug("News Hub capture failed for %s: %s", ref, exc)


def report_scrape(target_identifier: str, parsed: int, ingested: int = 0,
                  error: Optional[str] = None) -> None:
    """
    Tell DAEDALUS how a scrape pass went so dead sources become visible (Stage 39).

    ``parsed`` is how many entries the parser SAW (feed health), ``ingested`` how many
    were new (novelty). A healthy feed often ingests 0; only a broken one parses 0.
    Fail-soft — reporting must never break a scraper loop.
    """
    try:
        with httpx.Client(timeout=10.0) as client:
            client.post(
                f"{DAEDALUS_URL}/api/v1/landscape/internal/report",
                json={"target_identifier": target_identifier, "parsed": parsed,
                      "ingested": ingested, "error": error},
                headers={"X-Internal-Token": INTERNAL_API_TOKEN},
            )
    except Exception as exc:
        logger.debug("Scrape report failed for %s: %s", target_identifier, exc)
