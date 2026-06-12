"""
ORPHEUS — Contextual RAG Retrieval (Stage 21)
===============================================
Retrieves "Fresh Context Memory" for the cognitive brain. Before responding to a
social target, ORPHEUS embeds the target text, vector-searches MUNINN's clustered
KnowledgeFacts (via DAEDALUS pgvector), and filters strictly to the layers the
acting agent is subscribed to.

Two retrieval modes:
  • Forced Context — the operator pinned an exact fact on the Mission; we use it
    verbatim and skip the vector search entirely.
  • RAG — embed → similarity search → layer filter → inject top facts.

Fail-soft: any embedding/network failure yields an empty (but valid) context so
inference is never blocked.
"""

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("orpheus.rag")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
EMBED_MODEL_NAME = os.getenv("EMBED_MODEL_NAME", "nomic-embed-text")
EMBED_DIM = int(os.getenv("EMBED_DIM", "768"))

DAEDALUS_URL = os.getenv("DAEDALUS_URL", "http://daedalus:8000")
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "morpheus-internal-sync-key")

RAG_TOP_K = int(os.getenv("RAG_TOP_K", "4"))
# Discard weakly-related facts so the prompt isn't polluted with noise.
RAG_MIN_SIMILARITY = float(os.getenv("RAG_MIN_SIMILARITY", "0.5"))

_NO_CONTEXT = "No fresh context memory available for this topic."


def _embed(text: str) -> Optional[list]:
    """Embed text with nomic-embed-text via Ollama (None on failure)."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{OLLAMA_BASE_URL}/api/embeddings",
                json={"model": EMBED_MODEL_NAME, "prompt": text},
            )
            resp.raise_for_status()
            embedding = resp.json().get("embedding")
        if not embedding or len(embedding) != EMBED_DIM:
            logger.warning("Embedding had unexpected shape — skipping RAG.")
            return None
        return embedding
    except Exception as exc:
        logger.warning("Ollama embedding failed for RAG: %s", exc)
        return None


def fetch_fresh_context(
    post_text: str,
    subscriptions: list,
    forced_context: Optional[str] = None,
) -> str:
    """
    Resolve the Fresh Context Memory block injected into the LLM system prompt.

    forced_context (if provided) wins and bypasses the vector search entirely.
    Otherwise, embed the target post and retrieve subscribed-layer facts.
    """
    if forced_context and forced_context.strip():
        logger.info("Using operator-forced context (vector search bypassed).")
        return f"- [FORCED/OPERATOR]: {forced_context.strip()}"

    subscriptions = [s for s in (subscriptions or []) if s] or ["global"]

    embedding = _embed(post_text)
    if embedding is None:
        return _NO_CONTEXT

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                f"{DAEDALUS_URL}/api/v1/knowledge/internal/rag-search",
                json={"embedding": embedding, "layers": subscriptions, "limit": RAG_TOP_K},
                headers={"X-Internal-Token": INTERNAL_API_TOKEN},
            )
            resp.raise_for_status()
            matches = resp.json().get("matches", [])
    except Exception as exc:
        logger.warning("RAG search against DAEDALUS failed: %s", exc)
        return _NO_CONTEXT

    lines = []
    for m in matches:
        if float(m.get("similarity", 0.0)) < RAG_MIN_SIMILARITY:
            continue
        # Stage 22 — facts now carry multiple layers + categories. Surface the
        # layer intersection and themes so the persona has richer grounding.
        layers = m.get("landscape_layers") or ["global"]
        categories = m.get("categories") or []
        sim = m.get("similarity", 0.0)
        meta = "/".join(str(l).upper() for l in layers)
        if categories:
            meta += " · " + ", ".join(categories)
        lines.append(f"- [{meta} | relevance {sim:.2f}]: {m.get('content', '').strip()}")

    if not lines:
        return _NO_CONTEXT

    logger.info("Injected %d fresh-context fact(s) from layers=%s.", len(lines), subscriptions)
    return "\n".join(lines)
