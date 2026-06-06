"""
MUNINN — Long-Term Associative Memory Service
================================================
FastAPI wrapper over embedded ChromaDB.
Provides semantic search and storage of past dialog embeddings
for MORPHEUS AI agents.

Endpoints:
  POST /api/v1/memory/search  — Find past dialog matches by semantic similarity
  POST /api/v1/memory/save    — Store a new dialog summary as a vector embedding

Embedding model: intfloat/multilingual-e5-small (runs strictly on CPU).
"""

import logging
import os
import uuid
from contextlib import asynccontextmanager
from typing import Optional

import chromadb
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

# ── Logging ───────────────────────────────────────────────────────────────

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("muninn")

# ── Configuration ─────────────────────────────────────────────────────────

CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", "/app/chroma_db")
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "intfloat/multilingual-e5-small")
COLLECTION_NAME = "morpheus_agent_memory"

# Global references set during lifespan
chroma_client: Optional[chromadb.ClientAPI] = None
memory_collection: Optional[chromadb.Collection] = None
embedding_model: Optional[SentenceTransformer] = None


# ── Pydantic schemas (exact JSON contracts from bootstrap_protocols.md) ──

class MemorySearchRequest(BaseModel):
    agent_id: str
    opponent_id: str
    query_text: str


class MemoryMatch(BaseModel):
    text: str
    distance: float


class MemorySearchResponse(BaseModel):
    status: str
    matches: list[MemoryMatch]


class MemorySaveRequest(BaseModel):
    agent_id: str
    opponent_id: str
    dialog_summary: str


class MemorySaveResponse(BaseModel):
    status: str
    memory_id: str
    message: str


class HealthResponse(BaseModel):
    service: str
    status: str
    version: str
    collection_count: int


# ── Lifespan ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(application: FastAPI):
    """Initialize ChromaDB client, collection and embedding model on startup."""
    global chroma_client, memory_collection, embedding_model

    logger.info("MUNINN starting — initializing ChromaDB (persist_dir=%s)...", CHROMA_PERSIST_DIR)

    chroma_client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)

    memory_collection = chroma_client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    doc_count = memory_collection.count()
    logger.info(
        "MUNINN ready — collection '%s' loaded with %d documents.",
        COLLECTION_NAME,
        doc_count,
    )
    
    logger.info("Loading embedding model '%s' strictly on CPU...", EMBEDDING_MODEL_NAME)
    embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME, device='cpu')
    logger.info("Embedding model loaded.")

    yield
    logger.info("MUNINN shutting down.")


# ── FastAPI Application ───────────────────────────────────────────────────

app = FastAPI(
    title="MUNINN — MORPHEUS Long-Term Memory",
    description="Semantic vector memory service backed by embedded ChromaDB",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Health Check ──────────────────────────────────────────────────────────

@app.get("/", response_model=HealthResponse, tags=["System"])
def health_check() -> HealthResponse:
    """Service health and collection document count."""
    count = memory_collection.count() if memory_collection else 0
    return HealthResponse(
        service="muninn",
        status="operational",
        version="0.1.0",
        collection_count=count,
    )


# ── Memory Search ─────────────────────────────────────────────────────────

@app.post("/api/v1/memory/search", response_model=MemorySearchResponse, tags=["Memory"])
def search_memory(request: MemorySearchRequest) -> MemorySearchResponse:
    """
    Semantic search over past dialog embeddings.

    Finds the most relevant past conversation fragments between
    the specified agent and opponent, ranked by cosine similarity.
    """
    if memory_collection is None or embedding_model is None:
        raise HTTPException(status_code=503, detail="Services not initialized")

    # Generate embedding
    query_embedding = embedding_model.encode([request.query_text]).tolist()

    # Build a composite filter for agent+opponent scoping
    where_filter = {
        "$and": [
            {"agent_id": {"$eq": request.agent_id}},
            {"opponent_id": {"$eq": request.opponent_id}},
        ]
    }

    try:
        results = memory_collection.query(
            query_embeddings=query_embedding,
            n_results=5,
            where=where_filter,
        )
    except Exception as exc:
        logger.warning(
            "ChromaDB query failed (agent=%s, opponent=%s): %s — returning empty results.",
            request.agent_id,
            request.opponent_id,
            exc,
        )
        return MemorySearchResponse(status="success", matches=[])

    matches: list[MemoryMatch] = []
    if results and results["documents"] and results["documents"][0]:
        documents = results["documents"][0]
        distances = results["distances"][0] if results["distances"] else [0.0] * len(documents)
        for doc_text, dist in zip(documents, distances):
            matches.append(MemoryMatch(text=doc_text, distance=round(dist, 4)))

    logger.info(
        "Memory search: agent=%s, opponent=%s — %d matches found.",
        request.agent_id,
        request.opponent_id,
        len(matches),
    )
    return MemorySearchResponse(status="success", matches=matches)


# ── Memory Save ───────────────────────────────────────────────────────────

@app.post("/api/v1/memory/save", response_model=MemorySaveResponse, tags=["Memory"])
def save_memory(request: MemorySaveRequest) -> MemorySaveResponse:
    """
    Store a dialog summary as a vector embedding in ChromaDB.

    The dialog_summary is embedded using multilingual-e5-small (CPU)
    and indexed with agent_id + opponent_id metadata for scoped retrieval.
    """
    if memory_collection is None or embedding_model is None:
        raise HTTPException(status_code=503, detail="Services not initialized")

    memory_id = str(uuid.uuid4())

    # Generate embedding
    doc_embedding = embedding_model.encode([request.dialog_summary]).tolist()

    memory_collection.add(
        embeddings=doc_embedding,
        documents=[request.dialog_summary],
        metadatas=[
            {
                "agent_id": request.agent_id,
                "opponent_id": request.opponent_id,
            }
        ],
        ids=[memory_id],
    )

    logger.info(
        "Memory saved: id=%s, agent=%s, opponent=%s, length=%d chars.",
        memory_id,
        request.agent_id,
        request.opponent_id,
        len(request.dialog_summary),
    )
    return MemorySaveResponse(
        status="success",
        memory_id=memory_id,
        message=f"Dialog summary stored for agent {request.agent_id} ↔ {request.opponent_id}",
    )
