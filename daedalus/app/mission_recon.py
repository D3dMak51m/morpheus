"""
DAEDALUS — Mission reconnaissance (Stage 43)
==============================================
Before a mission speaks, it should know what it is talking about.

Until now there was no such phase at all: the flow went scan posts → relevance gate →
generate → publish, and the only grounding was up to four RAG facts pulled at
prompt-assembly time and thrown away afterwards. Nothing accumulated, so the team
argued from whatever happened to surface for that one comment.

This builds the mission's factual base once, into `mission_dossier`, where the whole
roster reads it.

Retrieval here is LEXICAL-FIRST, unlike the per-comment RAG, and that is a measured
decision rather than a stylistic one. Asked for the transport mission, the top 40
candidates by cosine were Novorossiysk transport, Trump/Ukraine, weightlifters, résumé
advice and a fire — while 57 genuinely transport-related facts sat in the corpus
unretrieved. `nomic-embed-text` simply does not separate topics here, so an
embedding-first recon admits nothing and reports "we know nothing", which is false.
Recon runs once per mission, not once per comment, so it can afford to scan the corpus
directly and let similarity only break ties.

A dossier padded with confident-looking irrelevancies is worse than a thin one, because
the bots will argue from it — so admission still requires shared vocabulary.

Read-only with respect to production knowledge: facts are copied into the dossier with
their source, never modified.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from sqlalchemy.dialects.postgresql import array
from sqlalchemy.orm import Session

from app.models import KnowledgeFact, Mission, MissionDossier
from app.router_knowledge import _content_terms

logger = logging.getLogger("daedalus.mission_recon")

# How much of the corpus a single recon pass reads. This is a batch job, so it scans
# rather than relying on an embedding search that demonstrably cannot find the topic.
RECON_SCAN_LIMIT = int(os.getenv("MISSION_RECON_SCAN_LIMIT", "3000"))
# How many facts a single recon pass may file.
RECON_MAX_FACTS = int(os.getenv("MISSION_RECON_MAX_FACTS", "12"))
# Share of the mission's vocabulary a fact must share to be admitted.
RECON_MIN_OVERLAP = float(os.getenv("MISSION_RECON_MIN_OVERLAP", "0.25"))
# Only facts this fresh are worth arguing from.
RECON_MAX_AGE_DAYS = int(os.getenv("MISSION_RECON_MAX_AGE_DAYS", "30"))


def mission_query(mission: Mission) -> str:
    """Everything the mission says about its subject, as one retrieval query."""
    parts = [
        mission.narrative_goal or "",
        mission.stance or "",
        mission.our_side or "",
        mission.opponent or "",
        " ".join(str(p) for p in (mission.key_points or [])),
    ]
    return "\n".join(p for p in parts if str(p).strip())


def run_recon(db: Session, mission_id: int, layers: list[str] | None = None) -> dict[str, Any]:
    """
    Build the mission's factual base from the knowledge store.

    Returns counts rather than the rows: the operator inspects the dossier itself, and
    a recon that admitted nothing is a real answer ("we know nothing about this yet"),
    not an error to paper over.
    """
    mission = db.query(Mission).filter(Mission.id == mission_id).first()
    if mission is None:
        raise ValueError("mission not found")

    query = mission_query(mission)
    if not query.strip():
        return {"scanned": 0, "admitted": 0, "filed": 0,
                "reason": "миссия не описана — нечего искать"}

    from datetime import datetime, timedelta, timezone
    from sqlalchemy import func

    q = db.query(KnowledgeFact)
    if layers:
        q = q.filter(KnowledgeFact.landscape_layers.op("?|")(array(layers)))
    if RECON_MAX_AGE_DAYS:
        cutoff = datetime.now(timezone.utc) - timedelta(days=RECON_MAX_AGE_DAYS)
        q = q.filter(func.coalesce(KnowledgeFact.published_at,
                                   KnowledgeFact.created_at) >= cutoff)
    rows = (q.order_by(func.coalesce(KnowledgeFact.published_at,
                                     KnowledgeFact.created_at).desc())
            .limit(RECON_SCAN_LIMIT).all())

    # Match on what is DISTINCTIVE about the mission, weighting each term by how rare
    # it is in the corpus we just scanned. A plain share-of-words test admits articles
    # that merely reuse the mission's filler — measured, the transport mission's first
    # two hits were «жизнь на новых массивах» and «35-летний путь развития», which
    # passed on «развитие» and «город» while saying nothing about transport. Rarity is
    # self-tuning, so no hand-maintained stopword list has to be kept in step with the
    # corpus.
    query_terms = _content_terms(query)
    if not query_terms:
        return {"scanned": len(rows), "admitted": 0, "filed": 0,
                "reason": "в описании миссии нет содержательных слов"}

    fact_terms = [(f, _content_terms(f.content or "")) for f in rows]
    total = len(fact_terms) or 1
    df = {t: 0 for t in query_terms}
    for _f, terms in fact_terms:
        for t in query_terms & terms:
            df[t] += 1
    # Classic IDF, floored at 0 so a term present everywhere simply stops counting.
    import math
    weight = {t: max(0.0, math.log(total / (1 + df[t]))) for t in query_terms}
    weight_total = sum(weight.values())
    if weight_total <= 0:
        return {"scanned": len(rows), "admitted": 0, "filed": 0,
                "reason": "все слова миссии встречаются повсеместно — уточните формулировку"}

    admitted: list[tuple[float, KnowledgeFact]] = []
    best = 0.0
    for fact, terms in fact_terms:
        shared = query_terms & terms
        if not shared:
            continue
        covered = sum(weight[t] for t in shared) / weight_total
        best = max(best, covered)
        if covered >= RECON_MIN_OVERLAP:
            admitted.append((covered, fact))
    admitted.sort(key=lambda x: -x[0])

    # The mission's own words that the corpus never mentions. This is the actionable
    # half of a recon that files nothing: "we found nothing" is a dead end, "the base
    # has not one article about пробки / полоса / трафик" tells the operator which
    # sources to add. Measured on the transport mission: its distinctive terms appeared
    # in 0-3 of 1249 facts, i.e. the swarm was about to argue from an empty base.
    missing = sorted((t for t in query_terms if df.get(t, 0) == 0), key=len, reverse=True)[:10]

    filed = 0
    for _covered, fact in admitted[:RECON_MAX_FACTS]:
        content = " ".join((fact.content or "").split())[:600]
        if not content:
            continue
        exists = (db.query(MissionDossier)
                  .filter(MissionDossier.mission_id == mission_id,
                          MissionDossier.kind == "fact",
                          MissionDossier.content == content)
                  .first())
        if exists is not None:
            continue
        db.add(MissionDossier(
            mission_id=mission_id, kind="fact", content=content,
            source_url=fact.source_url, added_by="recon", times_used=0,
        ))
        filed += 1
    db.commit()

    logger.info("Mission %s recon: scanned=%d admitted=%d filed=%d",
                mission_id, len(rows), len(admitted), filed)
    if filed:
        reason = ""
    elif missing:
        reason = ("В базе знаний нет материала по ключевым словам миссии: "
                  + ", ".join(missing)
                  + ". Добавьте источники по теме — иначе рой будет спорить без фактов.")
    else:
        reason = (f"Ничего не набрало порог {RECON_MIN_OVERLAP:.2f} "
                  f"(лучший кандидат {best:.2f}) — тема описана слишком общо.")
    return {"scanned": len(rows), "admitted": len(admitted), "filed": filed,
            "best_score": round(best, 3), "missing_terms": missing, "reason": reason}
