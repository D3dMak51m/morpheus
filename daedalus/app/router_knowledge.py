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

import html
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.orm import Session

from app import geo, refetch
from app.classifier import auto_classify_text
from app.database import get_db
from app.embeddings import generate_embedding
from app.models import AdminUser, KnowledgeFact, LANDSCAPE_LAYERS
from app.rbac import require_permission

logger = logging.getLogger("daedalus.router_knowledge")

router = APIRouter(prefix="/api/v1/knowledge", tags=["Cognitive Knowledge"])

INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "morpheus-internal-sync-key")

# Stage 39 — merge quality, measured on the live corpus rather than guessed.
#
# `nomic-embed-text` encodes language and register at least as strongly as topic:
#
#   true duplicates (same story, different outlet)   0.917 – 0.935
#   same topic, different story                      0.774 – 0.794
#   completely unrelated, same language              0.720 – 0.849
#
# The old 0.85 floor sat INSIDE the unrelated band, so the merger ate real news:
# one stored fact was 16 different posts from @burchakostida, and across the base
# 465 of 1255 ingested bodies (37%) had been discarded by false merges.
#
# Cosine alone still cannot carry the decision — on the LONG texts that actually
# reach production, unrelated pairs climbed to ~0.90 (the live log shows false merges
# at 0.854–0.900). So the floor is set just under the duplicate band and the real
# protection is lexical: a merge must also share concrete vocabulary. Validated on the
# measured pairs above, this rule is 8/8 where cosine-only was 7/8 at best.
MERGE_SIMILARITY_THRESHOLD = float(os.getenv("KNOWLEDGE_MERGE_THRESHOLD", "0.90"))
# A merge must share concrete vocabulary (entities/places) unless similarity is so
# high it stands on its own. Unrelated pairs measured 0.00–0.33 overlap; true
# duplicates 0.43–0.67.
MERGE_MIN_OVERLAP = float(os.getenv("KNOWLEDGE_MERGE_MIN_OVERLAP", "0.25"))
MERGE_CERTAIN_SIMILARITY = float(os.getenv("KNOWLEDGE_MERGE_CERTAIN", "0.96"))
# A cluster's tag list is a union, so it grew without bound: the 16-post fact carried
# 51 tags (`лавров` + `криптовалюта` + `музыка` + `узбекистан`…) and that soup is what
# /internal/by-geo matches on. Bound both lists; the fact's own tags come first.
MAX_MERGED_TAGS = int(os.getenv("KNOWLEDGE_MAX_MERGED_TAGS", "12"))
MAX_MERGED_CATEGORIES = int(os.getenv("KNOWLEDGE_MAX_MERGED_CATEGORIES", "8"))
# How many superseded wordings to keep on a cluster before dropping the oldest.
MAX_VARIANTS = int(os.getenv("KNOWLEDGE_MAX_VARIANTS", "5"))
# Stage 39 — freshness. "Current news" that is three weeks old is not context, it is
# misinformation the bot states with confidence; 93% of the corpus was over a week old
# and neither retrieval path had any age cutoff at all. 0 disables the filter.
DEFAULT_RAG_MAX_AGE_DAYS = int(os.getenv("KNOWLEDGE_RAG_MAX_AGE_DAYS", "14"))
DEFAULT_GEO_MAX_AGE_DAYS = int(os.getenv("KNOWLEDGE_GEO_MAX_AGE_DAYS", "7"))
# Stage 39 — how much of an article to keep. RSS entries used to arrive as 180-350
# character teasers; now the scrapers open the article itself (4-44x more text), so the
# old 1200 cap started biting mid-story. The prompt only ever quotes ~320 characters,
# but the extra body is what the lexical RAG gate matches on.
DEFAULT_FACT_MAX_LEN = int(os.getenv("KNOWLEDGE_FACT_MAX_LEN", "2400"))
# On a merge, how much longer the incoming telling must be to replace the stored one.
# The same story reaches us twice — as the feed's teaser and as the full article — and
# whichever arrives first should not be the one that sticks.
RICHER_CONTENT_RATIO = float(os.getenv("KNOWLEDGE_RICHER_RATIO", "1.5"))


# Stage 38 — markup/boilerplate that RSS bodies drag into the knowledge base.
_TAG_RE = re.compile(r"<[^>]+>")
_PREVIEW_RE = re.compile(r"(?:alt\s*=\s*[\"']?Preview[\"']?|src\s*=\s*[\"'][^\"']*[\"'])",
                         re.IGNORECASE)
_WS_RE = re.compile(r"\s+")

# Stage 39 — site furniture that survives HTML stripping and ends up inside the fact.
# Measured on the live corpus: 497 of 497 RT facts ended in "Читать далее" (the feed's
# read-more link text), and 44 of 90 daryo.uz facts were nothing BUT chrome — comment
# form, breadcrumb nav and a subscription ad. Both were embedded and handed to the bots
# as world knowledge.
_BOILERPLATE_PATTERNS = [
    # read-more / continue markers (ru / en / uz)
    r"читать\s+далее",
    r"читать\s+полностью",
    r"подробнее(?:\s+на\s+сайте)?",
    r"read\s+more",
    r"batafsil(?:\s+o[’'‘`]?qish)?",
    r"to[’'‘`]?liq\s+o[’'‘`]?qish",
    # comment widgets (uz)
    r"izoh\s+qoldirish\s+uchun,?\s*avval",
    r"izohlar",
    # subscription / advertising blocks (uz)
    r"obuna\s+bo[’'‘`]?lish[^.]{0,40}",
    r"reklama\s+bering",
    r"reklamalarsiz\s+sayt\s+mutolaasi",
    r"yaxshi\s+yangiliklar:\s*biznesingizni\s+biz\s+bilan\s+rivojlantiring",
    r"individual\s+yondashuv\s+va\s+eksklyuziv\s+materiallar",
    # generic subscribe/share furniture
    r"subscribe(?:\s+to\s+our\s+newsletter)?",
    r"поделиться(?:\s+в\s+соцсетях)?",
    r"подпис(?:ывайтесь|аться)[^.]{0,40}",
]
_BOILERPLATE_RE = re.compile("|".join(_BOILERPLATE_PATTERNS), re.IGNORECASE)
_URL_RE = re.compile(r"https?://\S+|\bt\.me/\S+", re.IGNORECASE)

# An immediately repeated phrase (1–8 words) is navigation, not prose:
# "O‘zbekiston O‘zbekiston Madaniyat O‘zbekiston O‘zbekiston Madaniyat".
_REPEAT_RE = re.compile(r"\b((?:[^\s]+\s+){0,7}?[^\s]+)(\s+\1\b)+", re.IGNORECASE)
# Extractors often emit "Title. Title <body>" — the same sentence twice in a row.
_DUP_SENTENCE_RE = re.compile(r"^(.{10,160}?)[.。!?]?\s+\1\b[.。!?]?\s*", re.IGNORECASE)


def _strip_trailing_crumbs(text: str) -> str:
    """
    Drop a breadcrumb tail left dangling after the last real sentence.

    daryo.uz extractions end "…10 ta noodatiy buyum. O‘zbekiston Madaniyat" — the
    site's section path, not part of the story. A crumb is recognised structurally,
    not by name: a short run of Capitalised words after the final sentence stop, with
    no lower-case word in it. The 40% guard keeps this away from real content, so a
    genuine sentence ending in names ("…встретился с Владимиром Путиным") survives.
    """
    s = (text or "").strip()
    idx = max(s.rfind("."), s.rfind("!"), s.rfind("?"))
    if idx < 0 or idx >= len(s) - 1:
        return s
    tail = s[idx + 1:].strip()
    words = tail.split()
    if not words or len(words) > 5 or len(tail) > 0.4 * len(s):
        return s
    if all(w[:1].isupper() or not w[:1].isalpha() for w in words):
        return s[: idx + 1].strip()
    return s


def strip_boilerplate(text: str) -> str:
    """
    Remove site furniture (read-more links, comment widgets, subscribe ads, nav runs).

    Deliberately conservative: it deletes known chrome and collapses verbatim
    repetitions, never trims real sentences. What is left may be empty — that is the
    honest outcome for a page whose body never extracted, and the caller decides.
    """
    s = text or ""
    # Raw links (often with tracking params) are noise twice over: they skew the
    # embedding and the comment-writing model may echo them verbatim into a post.
    s = _URL_RE.sub(" ", s)
    s = _BOILERPLATE_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    for _ in range(3):                       # nav runs can nest
        collapsed = _REPEAT_RE.sub(r"\1", s)
        if collapsed == s:
            break
        s = collapsed
    s = _DUP_SENTENCE_RE.sub(r"\1. ", s)
    s = _strip_trailing_crumbs(s)
    s = _WS_RE.sub(" ", s)
    # Leftover punctuation/separators from the excisions.
    s = re.sub(r"\s+([.,;:!?])", r"\1", s)
    s = re.sub(r"(?:[.,;:•|–—-]\s*){2,}", ". ", s)
    return s.strip(" .,;:•|–—-").strip()


def truncate_clean(text: str, max_len: int) -> str:
    """
    Cut to ``max_len`` on a sentence boundary, falling back to a word boundary.

    A hard slice ended facts mid-word ("…в результате од"), which reads as broken to
    the operator and gives the embedder a fragment token to chew on. Prefer the last
    full sentence; if that would throw away more than a third of the budget, keep the
    text and cut at the last space instead.
    """
    s = (text or "").strip()
    if len(s) <= max_len:
        return s
    window = s[:max_len]
    cut = max(window.rfind(". "), window.rfind("! "), window.rfind("? "),
              window.rfind(".\n"), window.rfind("…"))
    if cut >= max_len * 0.66:
        return window[:cut + 1].strip()
    space = window.rfind(" ")
    return (window[:space] if space > 0 else window).strip() + "…"


def clean_fact_text(raw: str, max_len: int = DEFAULT_FACT_MAX_LEN) -> str:
    """
    Strip HTML/markup and site furniture so a stored fact is a readable sentence.

    A fact is read twice: by the embedder (markup skews the vector) and by the LLM
    writing the comment (markup is noise it may echo). Both want plain prose.
    """
    s = raw or ""
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.DOTALL | re.IGNORECASE)
    s = _TAG_RE.sub(" ", s)
    s = _PREVIEW_RE.sub(" ", s)
    s = html.unescape(s)
    s = _WS_RE.sub(" ", s).strip()
    s = strip_boilerplate(s)
    return truncate_clean(s, max_len)


def _effective_age_column():
    """
    The timestamp a fact's age should be judged by.

    The source's publication date when we know it, our ingest time otherwise. Using
    `created_at` alone made a 19 May article "fresh" because kun.uz still linked it
    from the homepage on 11 August.
    """
    return func.coalesce(KnowledgeFact.published_at, KnowledgeFact.created_at)


def _clean_layers(values: Optional[list[str]]) -> list[str]:
    """Lowercase, validate against LANDSCAPE_LAYERS, dedupe (order-preserving)."""
    out: list[str] = []
    for raw in values or []:
        v = (raw or "").strip().lower()
        if v in LANDSCAPE_LAYERS and v not in out:
            out.append(v)
    return out


def _union(a: list, b: list, cap: Optional[int] = None) -> list:
    """Order-preserving union of two string lists, optionally bounded.

    ``a`` (the cluster's existing values) keeps priority — when the cap bites it is
    the newcomer's extra tags that are dropped, not the fact's own.
    """
    out = list(a or [])
    for x in (b or []):
        if x not in out:
            out.append(x)
    return out[:cap] if cap else out


# Stage 39 — lexical guard for merging. Cyrillic here must also cover Uzbek Cyrillic
# (ў/қ/ғ/ҳ), which is exactly the corpus where false merges were worst.
_WORD_RE = re.compile(r"[а-яёўқғҳa-z]{4,}", re.IGNORECASE)


def _content_terms(text: str) -> set[str]:
    """Concrete words of a text, crudely stemmed to tolerate Russian declension."""
    return {w[:6] for w in _WORD_RE.findall((text or "").lower())}


def _lexical_overlap(a: str, b: str) -> float:
    """
    Share of the SHORTER text's vocabulary that also appears in the longer one.

    Two tellings of one story share their entities (Путин / Аляска / Песков); two
    unrelated posts in the same language share only function words, which the
    4-character floor already removes. Asymmetric on purpose — a one-line headline
    merging into a full article is still a duplicate.
    """
    ta, tb = _content_terms(a), _content_terms(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


# ── Schemas ────────────────────────────────────────────────────────────────

class IngestRequest(BaseModel):
    content: str = Field(..., min_length=1)
    source_url: Optional[str] = None
    # Stage 22 — layers seeded by the HUGINN source config; merged with LLM layers.
    default_layers: list[str] = Field(default_factory=lambda: ["global"])
    # Optional precomputed embedding; if omitted DAEDALUS generates it.
    embedding: Optional[list[float]] = None
    # Stage 39 — when the SOURCE published this (RSS `published_parsed`, article
    # metadata). Freshness is about the story's age, not our scraping schedule.
    published_at: Optional[datetime] = None


class IngestResponse(BaseModel):
    action: str  # "inserted" | "merged"
    fact_id: int
    landscape_layers: list[str]
    categories: list[str]
    tags: list[str]
    geo_tags: list[str] = []
    source_count: int
    similarity: Optional[float] = None


class RagSearchRequest(BaseModel):
    embedding: list[float] = Field(..., min_length=1)
    layers: list[str] = Field(default_factory=lambda: ["global"])
    # Stage 38 — ORPHEUS pulls a wider candidate set and admits facts lexically
    # (embedding similarity alone does not separate topics on this corpus), so the
    # cap has to leave room for that re-ranking.
    limit: int = Field(5, ge=1, le=50)
    # Stage 39 — 0 disables the cutoff. RAG grounds a comment in what is happening
    # now; before this, 93% of the corpus was older than a week and nothing stopped
    # an 18-day-old story being injected as current context.
    max_age_days: int = Field(DEFAULT_RAG_MAX_AGE_DAYS, ge=0, le=365)


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
    geo_tags: list[str] = []
    sources: Optional[list[str]]
    source_count: int
    # Stage 39 — wordings this cluster absorbed; lets the operator audit a merge.
    variants: Optional[list[dict]] = None
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
    geo_tags: list[str],
    embedding: list[float],
    published_at: Optional[datetime] = None,
) -> tuple[str, KnowledgeFact, Optional[float]]:
    """
    Cluster ``content`` into MUNINN's memory.

    Dedup candidates are facts whose ``landscape_layers`` overlap the incoming
    layers (JSONB ``?|``). The nearest by cosine distance is merged only when it is
    a genuine duplicate — high cosine AND shared concrete vocabulary (Stage 39) —
    unioning layers/categories/tags, appending the source and preserving the
    superseded wording in ``variants``. Otherwise a new fact is inserted.
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
        overlap = _lexical_overlap(content, fact.content or "")
        # Cosine puts the candidate in the duplicate band; shared vocabulary confirms
        # it is the same STORY and not merely the same language.
        is_duplicate = similarity >= MERGE_SIMILARITY_THRESHOLD and (
            overlap >= MERGE_MIN_OVERLAP or similarity >= MERGE_CERTAIN_SIMILARITY
        )
        if is_duplicate:
            sources = list(fact.sources or [])
            if source_url and source_url not in sources:
                sources.append(source_url)
            fact.sources = sources
            fact.source_count = max(fact.source_count, len(sources))
            # Keep the wording we are superseding — a merge must never destroy news.
            # Stage 39 — and when the newcomer is the RICHER telling, it becomes the
            # canonical one. The same story arrives first as an RSS teaser and later as
            # the full article; first-seen-wins would have frozen the stub forever and
            # filed the real text away as a footnote.
            old_content = fact.content or ""
            superseded = content
            if len(content) > len(old_content) * RICHER_CONTENT_RATIO:
                fact.content = content
                superseded = old_content
                new_embedding = generate_embedding(content)
                if new_embedding is not None:
                    fact.embedding = new_embedding
                logger.info("KnowledgeFact %s content UPGRADED (%d → %d chars).",
                            fact.id, len(old_content), len(content))
            variants = list(fact.variants or [])
            variants.append({
                "content": superseded,
                "source_url": source_url,
                "at": datetime.now(timezone.utc).isoformat(),
                "similarity": round(similarity, 4),
            })
            fact.variants = variants[-MAX_VARIANTS:]
            # Enrich the cluster's classification with the new observation.
            fact.landscape_layers = _union(fact.landscape_layers, layers)
            fact.categories = _union(fact.categories, categories, MAX_MERGED_CATEGORIES)
            fact.tags = _union(fact.tags, tags, MAX_MERGED_TAGS)
            fact.geo_tags = _union(fact.geo_tags, geo_tags, MAX_MERGED_TAGS)
            if published_at and not fact.published_at:
                fact.published_at = published_at
            fact.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(fact)
            logger.info(
                "KnowledgeFact %s MERGED (layers=%s, similarity=%.3f, overlap=%.2f, sources=%d).",
                fact.id, fact.landscape_layers, similarity, overlap, fact.source_count,
            )
            return "merged", fact, similarity
        if similarity >= MERGE_SIMILARITY_THRESHOLD:
            # Near in embedding space but lexically unrelated — exactly the case that
            # used to swallow a distinct story. Keep it as its own fact.
            logger.info(
                "KnowledgeFact NOT merged into %s despite similarity=%.3f — lexical "
                "overlap only %.2f (< %.2f); storing as a separate fact.",
                fact.id, similarity, overlap, MERGE_MIN_OVERLAP,
            )

    sources = [source_url] if source_url else []
    fact = KnowledgeFact(
        content=content,
        source_url=source_url,
        landscape_layers=layers,
        categories=categories or [],
        tags=tags or [],
        geo_tags=geo_tags or [],
        embedding=embedding,
        published_at=published_at,
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


# Stage 39 — fragments the old web scraper stored as "news". It ingested the text of a
# homepage LINK, never the article, so CNN's cards arrived as photo credits
# ("Win McNamee/Getty Images"), teasers ("Charli XCX chasing cool") and widget chrome.
_JUNK_PATTERNS = [
    r"^[\w'’.\- ]{2,40}/(?:Getty Images|Reuters|AP|AFP|Bloomberg|CNN)\b",
    r"^(?:•\s*)?(?:Breaking News|Analysis|Live Updates|Video|Opinion)\b",
    r"(?:Getty Images|Getty|/Reuters|/AP|/AFP)\s*$",
    r"Show all\s*$",
    # Breadcrumb/section crumbs left where an article body never extracted.
    r"\b\w+\.(?:uz|com|ru|tj)\s+da\b",
]
_JUNK_RE = re.compile("|".join(_JUNK_PATTERNS), re.IGNORECASE)
# Below this, a "fact" is a headline fragment with no proposition in it.
MIN_FACT_LENGTH = int(os.getenv("KNOWLEDGE_MIN_FACT_LENGTH", "80"))


def is_junk_fact(content: str) -> bool:
    """True when a stored fact is scraper debris rather than a piece of news."""
    text = (content or "").strip()
    if len(text) < MIN_FACT_LENGTH:
        return True
    return bool(_JUNK_RE.search(text))


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

    # 0. Stage 38 — scrub markup BEFORE classifying/embedding. RSS bodies arrive with
    #    `<img align="left" alt="Preview" src=…>` tails (measured: 220 of 354 stored
    #    facts carried them), which poison both the embedding and the prompt the bot
    #    finally reads.
    content = clean_fact_text(request.content)
    if not content:
        raise HTTPException(status_code=400, detail="Empty content after cleaning.")
    # Stage 39 — reject site furniture at the door. daryo.uz does not expose article
    # bodies in its HTML, so every extraction there was the page's comment widget,
    # breadcrumbs and subscription ad; 44 of 90 stored daryo facts were exactly that,
    # embedded and offered to the bots as knowledge. One gate here covers every
    # scraper (RSS, web, Telegram) instead of each learning the rule separately.
    if is_junk_fact(content):
        logger.info("Ingest REJECTED as boilerplate (%d chars): %r", len(content), content[:120])
        raise HTTPException(status_code=422, detail="Content is site boilerplate, not a fact.")

    # 1. LLM auto-classification (before embedding / dedup).
    classification = await auto_classify_text(content)

    # 2. Merge classifier layers with the source's default layers.
    layers = _union(classification["layers"], _clean_layers(request.default_layers))
    if not layers:
        layers = ["global"]

    # 3. Embed (DAEDALUS-side) and 4. dedup/insert.
    embedding = _resolve_embedding(request.embedding, content)
    action, fact, similarity = _upsert_fact(
        db, content, request.source_url, layers,
        classification["categories"], classification["tags"],
        classification["geo_tags"], embedding, request.published_at,
    )
    return IngestResponse(
        action=action,
        fact_id=fact.id,
        landscape_layers=fact.landscape_layers,
        categories=fact.categories,
        tags=fact.tags,
        geo_tags=fact.geo_tags or [],
        source_count=fact.source_count,
        similarity=round(similarity, 4) if similarity is not None else None,
    )


class RefetchResponse(BaseModel):
    scanned: int
    upgraded: int
    skipped_host: int
    no_article: int
    not_richer: int
    avg_len_before: int
    avg_len_after: int


@router.post("/facts/refetch", response_model=RefetchResponse)
def refetch_facts(
    limit: int = 100,
    max_len: int = 400,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("db:edit")),
) -> RefetchResponse:
    """
    Re-read the article behind facts that were stored as RSS teasers.

    Those entries have rotated out of their feeds, so the scrapers will never revisit
    them and the stubs cannot self-heal — but each fact kept its `source_url`. The
    upgrade path is the same one a merge uses: richer text replaces the content, the
    embedding is recomputed (a vector built over a headline does not point where the
    article means), and the superseded wording is preserved in `variants`.

    `russian.rt.com` and `daryo.uz` are skipped — for them the stored teaser is already
    the better text (see app/refetch.py).
    """
    rows = (
        db.query(KnowledgeFact)
        .filter(func.length(KnowledgeFact.content) < max_len)
        .filter(KnowledgeFact.source_url.isnot(None))
        .order_by(KnowledgeFact.id.desc())
        .limit(limit)
        .all()
    )
    scanned = upgraded = skipped_host = no_article = not_richer = 0
    len_before = len_after = 0

    for fact in rows:
        scanned += 1
        len_before += len(fact.content or "")
        if refetch.should_skip(fact.source_url):
            skipped_host += 1
            len_after += len(fact.content or "")
            continue

        body = refetch.fetch_article_text(fact.source_url)
        if not body:
            no_article += 1
            len_after += len(fact.content or "")
            continue

        fresh = clean_fact_text(body)
        if is_junk_fact(fresh) or len(fresh) <= len(fact.content or "") * RICHER_CONTENT_RATIO:
            not_richer += 1
            len_after += len(fact.content or "")
            continue

        variants = list(fact.variants or [])
        variants.append({
            "content": fact.content,
            "source_url": fact.source_url,
            "at": datetime.now(timezone.utc).isoformat(),
            "note": "superseded by refetched article",
        })
        fact.variants = variants[-MAX_VARIANTS:]
        fact.content = fresh
        new_embedding = generate_embedding(fresh)
        if new_embedding is not None:
            fact.embedding = new_embedding
        fact.updated_at = datetime.now(timezone.utc)
        upgraded += 1
        len_after += len(fresh)

    db.commit()
    logger.info("Refetch: scanned=%d upgraded=%d skipped_host=%d no_article=%d not_richer=%d",
                scanned, upgraded, skipped_host, no_article, not_richer)
    return RefetchResponse(
        scanned=scanned, upgraded=upgraded, skipped_host=skipped_host,
        no_article=no_article, not_richer=not_richer,
        avg_len_before=round(len_before / scanned) if scanned else 0,
        avg_len_after=round(len_after / scanned) if scanned else 0,
    )


class CleanupResponse(BaseModel):
    scanned: int
    cleaned: int
    reembedded: int
    deleted: int
    # Stage 39
    retagged: int = 0
    geo_backfilled: int = 0
    junk_deleted: int = 0


@router.post("/facts/cleanup", response_model=CleanupResponse)
def cleanup_facts(
    limit: int = 500,
    reembed: bool = True,
    purge_junk: bool = True,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("db:edit")),
) -> CleanupResponse:
    """
    Repair facts stored before the Stage 38/39 ingest fixes.

    Four passes, all idempotent:
      * markup — rewrite content without HTML and recompute the embedding (a vector
        built over `<img … Preview src=…>` does not point where the sentence means);
      * tag soup — a merge unioned tags without bound, so a falsely-merged cluster
        carried up to 51 of them. `_union` keeps existing values FIRST, so truncating
        to MAX_MERGED_TAGS recovers the original story's own tags and drops what the
        false merges bolted on;
      * geo backfill — derive canonical `geo_tags` from the (now trimmed) tags, so the
        existing corpus is reachable by `by-geo` instead of only newly-ingested facts;
      * junk — delete scraper debris (photo credits, teaser fragments).

    The bodies destroyed by pre-Stage-39 merges are NOT recoverable — they were never
    stored. This only stops the surviving records from poisoning retrieval.
    """
    rows = db.query(KnowledgeFact).order_by(KnowledgeFact.id.desc()).limit(limit).all()
    scanned = cleaned = reembedded = deleted = retagged = geo_backfilled = junk_deleted = 0
    for fact in rows:
        scanned += 1

        # Judge what the fact will BE, not what it was: scrubbing furniture can turn a
        # 400-character page of nav into a bare headline, and that is junk even though
        # the original was long enough to pass.
        fresh = clean_fact_text(fact.content or "")
        if purge_junk and is_junk_fact(fresh):
            db.delete(fact)
            junk_deleted += 1
            continue

        if fresh != (fact.content or "").strip():
            if len(fresh) < 25:
                db.delete(fact)
                deleted += 1
                continue
            fact.content = fresh
            cleaned += 1
            if reembed:
                try:
                    fact.embedding = generate_embedding(fresh)
                    reembedded += 1
                except Exception as exc:   # embedding is best-effort; text is fixed anyway
                    logger.warning("Cleanup: re-embedding fact %s failed: %s", fact.id, exc)

        # Trim the accumulated tag soup and canonicalise what survives.
        tags = [geo.canonical_place(t) or geo.normalise_tag(t) for t in (fact.tags or [])]
        tags = [t for i, t in enumerate(tags) if t and t not in tags[:i]][:MAX_MERGED_TAGS]
        if tags != (fact.tags or []):
            fact.tags = tags
            retagged += 1

        # Grounded in the fact's own text — a falsely-merged cluster still carries the
        # other stories' place tags, and those are exactly what poisons `by-geo`.
        geo_tags = geo.places_in_text(geo.canonical_places(tags), fact.content or "")
        if geo_tags != (fact.geo_tags or []):
            fact.geo_tags = geo_tags
            geo_backfilled += 1

        fact.updated_at = datetime.now(timezone.utc)
    db.commit()
    logger.info("Knowledge cleanup: scanned=%d cleaned=%d reembedded=%d deleted=%d "
                "retagged=%d geo=%d junk=%d",
                scanned, cleaned, reembedded, deleted, retagged, geo_backfilled, junk_deleted)
    return CleanupResponse(scanned=scanned, cleaned=cleaned, reembedded=reembedded,
                           deleted=deleted, retagged=retagged,
                           geo_backfilled=geo_backfilled, junk_deleted=junk_deleted)


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
    query = (
        db.query(KnowledgeFact, distance)
        .filter(KnowledgeFact.landscape_layers.op("?|")(array(layers)))
    )
    if request.max_age_days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=request.max_age_days)
        query = query.filter(_effective_age_column() >= cutoff)
    rows = query.order_by(distance).limit(request.limit).all()

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


class LookupRequest(BaseModel):
    query: str = Field(..., min_length=2)
    layers: list[str] = Field(default_factory=lambda: ["global"])
    read_pages: int = Field(3, ge=1, le=6)
    # Something that changes (a score, a price, today's news) must be searched inside a
    # recent window; background about a subject must not be.
    recent: bool = True
    language: str = "ru"


@router.post("/internal/lookup")
def knowledge_lookup(
    request: LookupRequest,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Go and find out (Stage 47): search the web, read the top pages, file what was read
    into the knowledge base, and hand the findings back to the caller.

    This is the swarm's way out of a closed corpus. Recon calls it when the base holds
    nothing about a mission's subject; the comment path calls it when answering would
    otherwise mean inventing a fact that changes — a score, a price, today's news.
    Everything read is filed through the ordinary pipeline, so what one agent looked up
    the whole swarm knows afterwards.
    """
    if x_internal_token != INTERNAL_API_TOKEN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid internal token.")
    from app import tools
    return tools.lookup(db, request.query, layers=_clean_layers(request.layers) or ["global"],
                        read_pages=request.read_pages, recent=request.recent,
                        language=request.language)


@router.get("/internal/by-geo")
def facts_by_geo(
    terms: str = "",
    limit: int = 5,
    max_age_days: int = DEFAULT_GEO_MAX_AGE_DAYS,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Recent knowledge facts about a PLACE — facts whose canonical ``geo_tags`` overlap
    the given place terms (comma-separated, e.g. ``ташкент,узбекистан``). Layers are
    only a scope (global/regional/state/city), not a place, so we match on the place
    to ground a channel's comments in its OWN region's current news (Phase 2c).

    Stage 39 — matching moved from ``tags`` to ``geo_tags`` and both sides go through
    ``app.geo``. Matching raw tags meant the caller's Russian ``geo_label`` never met
    the corpus's English tags (`ташкент` hit 0 facts while `uzbekistan` had 24), and
    the only fact that did come back matched through a merged tag-soup rather than
    because it was about the place at all.

    ``max_age_days`` keeps this answering "what is happening HERE NOW" — an
    eighteen-day-old story is not current news (Stage 39).
    """
    if x_internal_token != INTERNAL_API_TOKEN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid internal token.")
    wanted = geo.expand_query_terms(
        t for t in terms.split(",") if t.strip() and len(t.strip()) >= 3
    )
    if not wanted:
        return {"facts": []}
    query = db.query(KnowledgeFact).filter(KnowledgeFact.geo_tags.op("?|")(array(wanted)))
    if max_age_days and max_age_days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        query = query.filter(_effective_age_column() >= cutoff)
    rows = (
        query.order_by(_effective_age_column().desc())
        .limit(min(max(limit, 1), 20))
        .all()
    )
    return {"facts": [{"content": f.content, "tags": f.tags or [],
                       "geo_tags": f.geo_tags or [],
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
        db, request.content, request.source_url, layers,
        classification["categories"], classification["tags"],
        classification["geo_tags"], embedding,
    )
    return IngestResponse(
        action=action,
        fact_id=fact.id,
        landscape_layers=fact.landscape_layers,
        categories=fact.categories,
        tags=fact.tags,
        geo_tags=fact.geo_tags or [],
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
