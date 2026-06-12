"""
HUGINN — Knowledge Ingestion (Stage 22)
=========================================
Generic scrapers (RSS / Web / general Telegram) route news EXCLUSIVELY into
MUNINN's clustered semantic memory — never into the execution queue / News Hub.

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
from typing import Optional

import httpx

logger = logging.getLogger("huginn.knowledge_ingest")

DAEDALUS_URL = os.getenv("DAEDALUS_URL", "http://daedalus:8000")
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "morpheus-internal-sync-key")


def ingest_knowledge(text: str, source_url: Optional[str], default_layers=None) -> None:
    """
    Push raw scraped text to DAEDALUS for classification + clustering into MUNINN.

    ``default_layers`` is the list of landscape layers configured on the source;
    DAEDALUS merges them with the LLM-extracted layers. Fail-soft.
    """
    text = (text or "").strip()
    if not text:
        return
    if not default_layers:
        default_layers = ["global"]

    try:
        with httpx.Client(timeout=90.0) as client:
            resp = client.post(
                f"{DAEDALUS_URL}/api/v1/knowledge/internal/ingest",
                json={
                    "content": text[:4000],
                    "source_url": source_url,
                    "default_layers": default_layers,
                },
                headers={"X-Internal-Token": INTERNAL_API_TOKEN},
            )
            resp.raise_for_status()
            data = resp.json()
        logger.info(
            "Knowledge ingest: %s fact #%s (layers=%s, cluster_size=%s).",
            data.get("action"), data.get("fact_id"),
            data.get("landscape_layers"), data.get("source_count"),
        )
    except Exception as exc:
        logger.warning("Failed to ingest knowledge into DAEDALUS: %s", exc)
