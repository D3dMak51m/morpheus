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

from app import textutil

logger = logging.getLogger("orpheus.rag")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
EMBED_MODEL_NAME = os.getenv("EMBED_MODEL_NAME", "nomic-embed-text")
EMBED_DIM = int(os.getenv("EMBED_DIM", "768"))

DAEDALUS_URL = os.getenv("DAEDALUS_URL", "http://daedalus:8000")
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "morpheus-internal-sync-key")

RAG_TOP_K = int(os.getenv("RAG_TOP_K", "4"))
# Stage 38 — retrieval quality, measured rather than guessed.
#
# On this corpus `nomic-embed-text` does NOT separate topics: a traffic-jam query
# scores unrelated war/politics news at 0.72–0.84, i.e. the same band as genuinely
# relevant facts. No absolute threshold can split that (the old floor of 0.5 admitted
# everything — which is why every prompt carried four confident-looking but unrelated
# facts and the operator "didn't notice RAG affecting the answers").
#
# So similarity is used only to fetch CANDIDATES; admission is decided lexically:
# a fact must share concrete vocabulary with the post/mission, or be similar far
# beyond the noise band. Precision over recall — an empty context block is honest
# and harmless, a wrong one actively misleads the model.
RAG_CANDIDATES = int(os.getenv("RAG_CANDIDATES", "30"))
# Share of the query's keywords a fact must contain to be admitted (>0 = at least one).
RAG_MIN_OVERLAP = float(os.getenv("RAG_MIN_OVERLAP", "0.12"))
# Similarity so high it stands on its own even without shared words.
RAG_STRONG_SIMILARITY = float(os.getenv("RAG_STRONG_SIMILARITY", "0.90"))

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


def _lexical_overlap(query_words: set, content: str) -> float:
    """Share of the query's concrete words that actually appear in the fact."""
    if not query_words:
        return 0.0
    body = {w[:6] for w in textutil._TOKEN_RE.findall((content or "").lower())}
    hits = sum(1 for w in query_words if w[:6] in body)
    return hits / len(query_words)


def fetch_fresh_context(
    post_text: str,
    subscriptions: list,
    forced_context: Optional[str] = None,
    mission_goal: str = "",
    mission_stance: str = "",
) -> str:
    """
    Resolve the Fresh Context Memory block injected into the LLM system prompt.

    Stage 38 — three changes, each measured:
      * the query is the post **plus the mission's goal/stance** (retrieving on the
        post alone means a mission about Argentina never finds anything about it);
      * candidates are re-ranked by lexical overlap, so a fact that shares concrete
        words beats one that merely sits nearby in embedding space;
      * a fact must share mission/post vocabulary (or clear the deliberately high
        strong-similarity escape hatch) — otherwise we honestly return "no context"
        instead of padding the prompt with confident-looking noise.

    forced_context (if provided) wins and bypasses the vector search entirely.
    """
    if forced_context and forced_context.strip():
        logger.info("Using operator-forced context (vector search bypassed).")
        return f"- [FORCED/OPERATOR]: {forced_context.strip()}"

    subscriptions = [s for s in (subscriptions or []) if s] or ["global"]

    # The mission's own words matter as much as the post's: they are what the comment
    # must be grounded in. Post first (it is the concrete situation), mission second.
    query = "\n".join(t for t in (post_text or "", mission_goal or "", mission_stance or "") if t.strip())
    embedding = _embed(query)
    if embedding is None:
        return _NO_CONTEXT

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                f"{DAEDALUS_URL}/api/v1/knowledge/internal/rag-search",
                json={"embedding": embedding, "layers": subscriptions, "limit": RAG_CANDIDATES},
                headers={"X-Internal-Token": INTERNAL_API_TOKEN},
            )
            resp.raise_for_status()
            matches = resp.json().get("matches", [])
    except Exception as exc:
        logger.warning("RAG search against DAEDALUS failed: %s", exc)
        return _NO_CONTEXT

    if not matches:
        return _NO_CONTEXT

    query_words = set(textutil.keywords(post_text, mission_goal, mission_stance, limit=14))
    admitted = []
    for m in matches:
        sim = float(m.get("similarity", 0.0))
        overlap = _lexical_overlap(query_words, m.get("content", ""))
        if overlap >= RAG_MIN_OVERLAP or sim >= RAG_STRONG_SIMILARITY:
            admitted.append((overlap, sim, m))
    # Best lexical match first; similarity only breaks ties.
    admitted.sort(key=lambda x: (-x[0], -x[1]))

    lines = []
    for overlap, sim, m in admitted[:RAG_TOP_K]:
        layers = m.get("landscape_layers") or ["global"]
        categories = m.get("categories") or []
        meta = "/".join(str(l).upper() for l in layers)
        if categories:
            meta += " · " + ", ".join(categories)
        content = textutil.clean_post_text(m.get("content", ""), max_len=320)
        if content:
            lines.append(f"- [{meta} | match {overlap:.0%}/{sim:.2f}]: {content}")

    if not lines:
        logger.info("RAG: nothing shared vocabulary with the query (best sim=%.2f, "
                    "keywords=%s) — injecting nothing.",
                    float(matches[0].get("similarity", 0.0)), sorted(query_words)[:6])
        return _NO_CONTEXT

    logger.info("Injected %d fresh-context fact(s) (top overlap=%.0f%%) from layers=%s.",
                len(lines), admitted[0][0] * 100, subscriptions)
    return "\n".join(lines)
