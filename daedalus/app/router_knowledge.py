"""
DAEDALUS — Cognitive Knowledge Router (Stage 22)
==================================================
The control surface for MUNINN's clustered semantic memory (``KnowledgeFact``)
and the social tactical targets (``SocialPostTarget``). Bifurcates epistemology
(what the swarm *knows*) from tactics (what it *acts against*).

Stage 22 upgrades:
  • Ingestion runs an LLM auto-classifier first → multi-dimensional
    ``landscape_layers`` + ``categories`` + ``tags``.
  • DAEDALUS generates the vector embedding itself (HUGINN only sends text).
  • RAG retrieval filters by ARRAY INTERSECTION (JSONB ``?|`` overlap) between a
    fact's ``landscape_layers`` and the agent's ``context_subscriptions``.

Endpoints
---------
Internal (token-secured, consumed by HUGINN / ORPHEUS):
  POST /api/v1/knowledge/internal/ingest      — classify → embed → dedup/insert
  POST /api/v1/knowledge/internal/rag-search  — vector search ∩ subscribed layers

Operator (JWT, consumed by the Muninn Explorer UI):
  GET    /api/v1/knowledge/facts          — browse clusters (optional layer filter)
  POST   /api/v1/knowledge/facts/inject   — manually inject a fact into memory
  DELETE /api/v1/knowledge/facts/{id}     — purge a fact
  GET    /api/v1/knowledge/stats          — per-layer cluster counts
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.orm import Session

from app.classifier import auto_classify_text
from app.database import get_db
from app.embeddings import generate_embedding
from app.models import AdminUser, KnowledgeFact, LANDSCAPE_LAYERS
from app.rbac import require_permission

logger = logging.getLogger("daedalus.router_knowledge")

router = APIRouter(prefix="/api/v1/knowledge", tags=["Cognitive Knowledge"])

INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "morpheus-internal-sync-key")

# Cosine similarity threshold above which two facts are considered the same story.
MERGE_SIMILARITY_THRESHOLD = float(os.getenv("KNOWLEDGE_MERGE_THRESHOLD", "0.85"))


def _clean_layers(values: Optional[list[str]]) -> list[str]:
    """Lowercase, validate against LANDSCAPE_LAYERS, dedupe (order-preserving)."""
    out: list[str] = []
    for raw in values or []:
        v = (raw or "").strip().lower()
        if v in LANDSCAPE_LAYERS and v not in out:
            out.append(v)
    return out


def _union(a: list, b: list) -> list:
    """Order-preserving union of two string lists."""
    out = list(a or [])
    for x in (b or []):
        if x not in out:
            out.append(x)
    return out


# ── Schemas ────────────────────────────────────────────────────────────────

class IngestRequest(BaseModel):
    content: str = Field(..., min_length=1)
    source_url: Optional[str] = None
    # Stage 22 — layers seeded by the HUGINN source config; merged with LLM layers.
    default_layers: list[str] = Field(default_factory=lambda: ["global"])
    # Optional precomputed embedding; if omitted DAEDALUS generates it.
    embedding: Optional[list[float]] = None


class IngestResponse(BaseModel):
    action: str  # "inserted" | "merged"
    fact_id: int
    landscape_layers: list[str]
    categories: list[str]
    tags: list[str]
    source_count: int
    similarity: Optional[float] = None


class RagSearchRequest(BaseModel):
    embedding: list[float] = Field(..., min_length=1)
    layers: list[str] = Field(default_factory=lambda: ["global"])
    limit: int = Field(5, ge=1, le=20)


class RagFact(BaseModel):
    id: int
    content: str
    landscape_layers: list[str]
    categories: list[str]
    tags: list[str]
    source_url: Optional[str]
    similarity: float


class RagSearchResponse(BaseModel):
    status: str
    matches: list[RagFact]


class FactResponse(BaseModel):
    id: int
    content: str
    source_url: Optional[str]
    landscape_layers: list[str]
    categories: list[str]
    tags: list[str]
    sources: Optional[list[str]]
    source_count: int
    timestamp: int
    created_at: Any
    updated_at: Any

    class Config:
        from_attributes = True


class InjectRequest(BaseModel):
    content: str = Field(..., min_length=1)
    source_url: Optional[str] = "manual://operator-injection"
    # Operator-selected layers (multi-select); unioned with LLM-classified layers.
    layers: list[str] = Field(default_factory=lambda: ["global"])

    @field_validator("layers")
    @classmethod
    def _valid_layers(cls, v: list[str]) -> list[str]:
        cleaned = _clean_layers(v)
        if not cleaned:
            raise ValueError(f"layers must contain at least one of {list(LANDSCAPE_LAYERS)}")
        return cleaned


# ── Core dedup-or-insert (shared by ingest + inject) ───────────────────────

def _upsert_fact(
    db: Session,
    content: str,
    source_url: Optional[str],
    landscape_layers: list[str],
    categories: list[str],
    tags: list[str],
    embedding: list[float],
) -> tuple[str, KnowledgeFact, Optional[float]]:
    """
    Cluster ``content`` into MUNINN's memory.

    Dedup candidates are facts whose ``landscape_layers`` overlap the incoming
    layers (JSONB ``?|``). The nearest by cosine distance is merged when
    similarity ≥ threshold — unioning layers/categories/tags and appending the
    source. Otherwise a new fact is inserted.
    """
    layers = landscape_layers or ["global"]

    distance = KnowledgeFact.embedding.cosine_distance(embedding).label("distance")
    nearest = (
        db.query(KnowledgeFact, distance)
        .filter(KnowledgeFact.landscape_layers.op("?|")(array(layers)))
        .order_by(distance)
        .limit(1)
        .first()
    )

    if nearest is not None:
        fact, dist = nearest
        similarity = 1.0 - float(dist)
        if similarity >= MERGE_SIMILARITY_THRESHOLD:
            sources = list(fact.sources or [])
            if source_url and source_url not in sources:
                sources.append(source_url)
            fact.sources = sources
            fact.source_count = max(fact.source_count, len(sources))
            # Enrich the cluster's classification with the new observation.
            fact.landscape_layers = _union(fact.landscape_layers, layers)
            fact.categories = _union(fact.categories, categories)
            fact.tags = _union(fact.tags, tags)
            fact.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(fact)
            logger.info(
                "KnowledgeFact %s MERGED (layers=%s, similarity=%.3f, sources=%d).",
                fact.id, fact.landscape_layers, similarity, fact.source_count,
            )
            return "merged", fact, similarity

    sources = [source_url] if source_url else []
    fact = KnowledgeFact(
        content=content,
        source_url=source_url,
        landscape_layers=layers,
        categories=categories or [],
        tags=tags or [],
        embedding=embedding,
        sources=sources,
        source_count=len(sources),
    )
    db.add(fact)
    db.commit()
    db.refresh(fact)
    logger.info("KnowledgeFact %s INSERTED (layers=%s).", fact.id, fact.landscape_layers)
    return "inserted", fact, None


def _resolve_embedding(request_embedding: Optional[list[float]], content: str) -> list[float]:
    """Use the provided embedding, else generate one server-side. Raises 503 on failure."""
    embedding = request_embedding or generate_embedding(content)
    if embedding is None:
        raise HTTPException(
            status_code=503,
            detail="Embedding service unavailable. Ensure Ollama is running and "
                   "`ollama pull nomic-embed-text` has completed on the host.",
        )
    return embedding


# ── Internal endpoints (HUGINN / ORPHEUS) ──────────────────────────────────

@router.post("/internal/ingest", response_model=IngestResponse)
async def ingest_fact(
    request: IngestRequest,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
    db: Session = Depends(get_db),
) -> IngestResponse:
    """
    HUGINN ingest: auto-classify (LLM) → embed → dedup/insert into MUNINN.

    Layer resolution: LLM-extracted layers ∪ the source's default_layers. If the
    union is empty (classifier failed AND no defaults), fall back to ["global"].
    """
    if x_internal_token != INTERNAL_API_TOKEN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid internal token.")

    # 1. LLM auto-classification (before embedding / dedup).
    classification = await auto_classify_text(request.content)

    # 2. Merge classifier layers with the source's default layers.
    layers = _union(classification["layers"], _clean_layers(request.default_layers))
    if not layers:
        layers = ["global"]

    # 3. Embed (DAEDALUS-side) and 4. dedup/insert.
    embedding = _resolve_embedding(request.embedding, request.content)
    action, fact, similarity = _upsert_fact(
        db, request.content, request.source_url,
        layers, classification["categories"], classification["tags"], embedding,
    )
    return IngestResponse(
        action=action,
        fact_id=fact.id,
        landscape_layers=fact.landscape_layers,
        categories=fact.categories,
        tags=fact.tags,
        source_count=fact.source_count,
        similarity=round(similarity, 4) if similarity is not None else None,
    )


@router.post("/internal/rag-search", response_model=RagSearchResponse)
def rag_search(
    request: RagSearchRequest,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
    db: Session = Depends(get_db),
) -> RagSearchResponse:
    """
    ORPHEUS RAG: return facts whose ``landscape_layers`` intersect the agent's
    subscriptions (JSONB ``?|`` overlap), ranked by cosine similarity.
    """
    if x_internal_token != INTERNAL_API_TOKEN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid internal token.")

    layers = _clean_layers(request.layers)
    if not layers:
        return RagSearchResponse(status="success", matches=[])

    distance = KnowledgeFact.embedding.cosine_distance(request.embedding).label("distance")
    rows = (
        db.query(KnowledgeFact, distance)
        .filter(KnowledgeFact.landscape_layers.op("?|")(array(layers)))
        .order_by(distance)
        .limit(request.limit)
        .all()
    )

    matches = [
        RagFact(
            id=fact.id,
            content=fact.content,
            landscape_layers=fact.landscape_layers or [],
            categories=fact.categories or [],
            tags=fact.tags or [],
            source_url=fact.source_url,
            similarity=round(1.0 - float(dist), 4),
        )
        for fact, dist in rows
    ]
    logger.info("RAG search ∩ layers=%s → %d matches.", layers, len(matches))
    return RagSearchResponse(status="success", matches=matches)


@router.get("/internal/by-geo")
def facts_by_geo(
    terms: str = "",
    limit: int = 5,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Recent knowledge facts about a PLACE — facts whose ``tags`` overlap the given place
    terms (comma-separated, e.g. ``ташкент,узбекистан``). Layers are only a scope
    (global/regional/state/city), not a place, so we match on the place tags to ground a
    channel's comments in its OWN region's current news (Phase 2c). Newest first.
    """
    if x_internal_token != INTERNAL_API_TOKEN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid internal token.")
    wanted = [t.strip().lower() for t in terms.split(",") if t.strip() and len(t.strip()) >= 3]
    if not wanted:
        return {"facts": []}
    rows = (
        db.query(KnowledgeFact)
        .filter(KnowledgeFact.tags.op("?|")(array(wanted)))
        .order_by(KnowledgeFact.created_at.desc())
        .limit(min(max(limit, 1), 20))
        .all()
    )
    return {"facts": [{"content": f.content, "tags": f.tags or [],
                       "categories": f.categories or []} for f in rows]}


# ── Operator endpoints (Muninn Explorer UI) ────────────────────────────────

@router.get("/facts", response_model=dict[str, Any])
def list_facts(
    layer: Optional[str] = None,
    q: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("monitoring:view")),
) -> dict[str, Any]:
    """Browse stored KnowledgeFact clusters, newest first; filter by layer and/or
    a free-text query (matches the fact content OR its source — e.g. a channel)."""
    query = db.query(KnowledgeFact)
    if layer:
        ln = layer.strip().lower()
        if ln in LANDSCAPE_LAYERS:
            query = query.filter(KnowledgeFact.landscape_layers.op("?|")(array([ln])))
    if q and q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(or_(KnowledgeFact.content.ilike(like),
                                 KnowledgeFact.source_url.ilike(like)))
    total = query.count()
    facts = query.order_by(KnowledgeFact.updated_at.desc()).offset(skip).limit(limit).all()
    return {
        "facts": [FactResponse.model_validate(f) for f in facts],
        "total": total,
    }


@router.get("/stats", response_model=dict[str, Any])
def knowledge_stats(
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("monitoring:view")),
) -> dict[str, Any]:
    """Per-layer cluster counts (a fact counts toward every layer it carries)."""
    counts = {layer: 0 for layer in LANDSCAPE_LAYERS}
    for layer in LANDSCAPE_LAYERS:
        counts[layer] = (
            db.query(KnowledgeFact)
            .filter(KnowledgeFact.landscape_layers.op("?|")(array([layer])))
            .count()
        )
    return {"by_layer": counts, "total": db.query(KnowledgeFact).count()}


@router.post("/facts/inject", response_model=IngestResponse, status_code=201)
async def inject_fact(
    request: InjectRequest,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("agents:manage")),
) -> IngestResponse:
    """
    Operator manually injects a fact. DAEDALUS auto-classifies for categories/tags
    and unions the LLM layers with the operator's explicit selections, embeds the
    text, then runs the same dedup/insert clustering path.
    """
    classification = await auto_classify_text(request.content)
    layers = _union(_clean_layers(request.layers), classification["layers"]) or ["global"]

    embedding = _resolve_embedding(None, request.content)
    action, fact, similarity = _upsert_fact(
        db, request.content, request.source_url,
        layers, classification["categories"], classification["tags"], embedding,
    )
    return IngestResponse(
        action=action,
        fact_id=fact.id,
        landscape_layers=fact.landscape_layers,
        categories=fact.categories,
        tags=fact.tags,
        source_count=fact.source_count,
        similarity=round(similarity, 4) if similarity is not None else None,
    )


@router.delete("/facts/{fact_id}")
def delete_fact(
    fact_id: int,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("agents:manage")),
) -> dict[str, str]:
    """Purge a single KnowledgeFact from memory."""
    fact = db.query(KnowledgeFact).filter(KnowledgeFact.id == fact_id).first()
    if not fact:
        raise HTTPException(status_code=404, detail="Knowledge fact not found.")
    db.delete(fact)
    db.commit()
    return {"status": "success", "message": f"KnowledgeFact {fact_id} purged."}
