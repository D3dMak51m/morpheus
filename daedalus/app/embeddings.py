"""
DAEDALUS — Embedding Helper (Stage 21)
========================================
Thin client over the host-side Ollama embeddings endpoint. Used when an operator
manually injects a KnowledgeFact through the Muninn Explorer UI — Daedalus must
embed the free text server-side so it lands in the same cosine space HUGINN and
ORPHEUS use.

Embedding model: nomic-embed-text (768-dim) via Ollama `/api/embeddings`.
"""

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("daedalus.embeddings")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
EMBED_MODEL_NAME = os.getenv("EMBED_MODEL_NAME", "nomic-embed-text")
EMBED_DIM = int(os.getenv("EMBED_DIM", "768"))


def generate_embedding(text: str) -> Optional[list[float]]:
    """
    Return the embedding vector for ``text`` (or None on failure).

    The caller decides how to handle a None result; we never fabricate a vector.
    """
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
            logger.error(
                "Embedding from Ollama had unexpected shape (got %s, want %d).",
                len(embedding) if embedding else None,
                EMBED_DIM,
            )
            return None
        return embedding
    except Exception as exc:
        logger.error("Failed to generate embedding via Ollama (%s): %s", EMBED_MODEL_NAME, exc)
        return None
