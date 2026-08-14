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
import re
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
# How many of the mission's subject words a fact must name to count as being about it.
# Two, because one is regularly a homonym: on the transport mission a single «трафик»
# admitted a story about footfall in electronics shops, and a single «развяз» admitted
# «развязать войну».
RECON_MIN_SUBJECTS = int(os.getenv("MISSION_RECON_MIN_SUBJECTS", "2"))
# ...or the subject named this many times, which is what separates an article ABOUT the
# subject from one that mentions it in passing.
RECON_MIN_MENTIONS = int(os.getenv("MISSION_RECON_MIN_MENTIONS", "3"))
# Below this many filed facts the mission goes and searches the web for its own subject
# rather than reporting a dead end.
RECON_SEARCH_BELOW = int(os.getenv("MISSION_RECON_SEARCH_BELOW", "3"))
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


def subject_terms(mission: Mission) -> list[str]:
    """
    The mission's SUBJECT, separated from its argument (Stage 47).

    Measured, and this is the whole reason the previous recon filed nothing: a mission's
    text is mostly the case it intends to make («расширенные дороги быстро заполняются
    новым трафиком»), and no news article restates someone's argument. Matching on all
    of it asked the corpus for an article that argues our side for us.

    IDF cannot separate the two either — on a 1594-fact corpus scattered across topics,
    ordinary words look rare («людей» 21, «нужен» 5, «дороги» 10) and a handful of them
    outweighs a real topical hit, so every admission rule tried ranked a boat sinking in
    Zimbabwe above a story about bus fares.

    One short generation does what statistics cannot: name the subject. It is asked for
    a plain list of words — never JSON, which this model answers badly — and it falls
    back to the mission's own nouns if the model is unavailable.
    """
    from app.classifier import ask_llm_short

    described = "\n".join(x for x in [
        mission.title or "", mission.narrative_goal or "",
        mission.our_side or "", mission.opponent or "",
        " ".join(str(p) for p in (mission.key_points or []))[:400],
    ] if x.strip())
    prompt = (
        "Вот описание темы, по которой мы собираем новости.\n\n"
        f"{described[:900]}\n\n"
        "Выпиши 5–8 слов, которыми ЭТА ТЕМА называется в новостях: предметы, места, "
        "явления. Только существительные в начальной форме, через запятую.\n"
        "Не пиши наших доводов и оценок («лучше», «решает», «нужен») — только предмет."
    )
    raw = ask_llm_short(prompt, max_tokens=60)
    terms = [w.strip(" .;:«»\"'()").lower() for w in re.split(r"[,\n]+", raw or "") if w.strip()]
    terms = [t for t in terms if 3 <= len(t) <= 24 and " " not in t][:8]
    if terms:
        logger.info("Mission %s subject terms: %s", mission.id, terms)
        return terms
    # No model: fall back to the mission's own distinctive words. Worse, but honest —
    # and recon will report what it could not find either way.
    fallback = [w for w in _content_terms(mission.title or "") | _content_terms(mission.our_side or "")]
    return sorted(fallback)[:8]


def run_recon(db: Session, mission_id: int, layers: list[str] | None = None,
              search_web: bool = True,
              prior_search: dict[str, Any] | None = None) -> dict[str, Any]:
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

    # Match on the mission's SUBJECT (Stage 47), not on everything it says. See
    # `subject_terms`: a mission's text is mostly its own argument, which no article
    # repeats, and IDF over this corpus cannot tell a topical word from an ordinary one.
    subjects = subject_terms(mission)
    subject_stems = {s[:6] for s in subjects if s}
    if not subject_stems:
        return {"scanned": len(rows), "admitted": 0, "filed": 0,
                "reason": "не удалось определить предмет миссии — уточните описание"}
    # Where the mission works. A fact about buses in another country is about buses,
    # not about this mission: an article on Russian public transport is a true fact and
    # a useless one under a Tashkent post.
    places = mission_places(db, mission)
    place_stems = {p[:6] for p in places}

    fact_terms = [(f, _content_terms(f.content or "")) for f in rows]
    total = len(fact_terms) or 1
    df = {t: 0 for t in subject_stems}
    for _f, terms in fact_terms:
        for t in subject_stems & terms:
            df[t] += 1

    # A fact is about the subject when it names at least RECON_MIN_SUBJECTS of the
    # subject's own words. Counting distinct subject words (rather than summing weights)
    # is what stops a single homonym from carrying an article in: «трафик» alone brought
    # in shop footfall, «развяз» brought in «развязать войну», while a piece that says
    # both «автобус» and «метро» is about transport and nothing else.
    admitted: list[tuple[int, KnowledgeFact]] = []
    best = 0
    for fact, terms in fact_terms:
        low = (fact.content or "").lower()
        # How often the subject is named, not merely whether it is. Measured on this
        # corpus: the article actually about Tashkent public transport names the subject
        # six times, while a hotel sale, a newspaper's anniversary and a drone strike
        # each contain exactly one passing «дороги» — and set membership could not tell
        # them apart, so the dossier filled with true facts about nothing relevant.
        occurrences = sum(low.count(st) for st in subject_stems)
        distinct = len(subject_stems & terms)
        # A named place is evidence too — and where the mission has one, a fact without
        # it is someone else's news: an article on Russian public transport is true and
        # useless under a Tashkent post.
        here = bool(place_stems & terms) or bool(place_stems & _content_terms(
            " ".join(fact.geo_tags or [])))
        if place_stems and not here:
            continue
        score = occurrences + distinct
        best = max(best, score)
        if occurrences >= RECON_MIN_MENTIONS or distinct >= RECON_MIN_SUBJECTS:
            admitted.append((score, fact))
    admitted.sort(key=lambda x: -x[0])

    # The subject words the corpus never mentions. This is the actionable half of a
    # recon that files nothing — and, since Stage 47, the query the swarm goes and
    # searches with instead of waiting for a general feed to mention Tashkent buses.
    missing = sorted((t for t in subject_stems if df.get(t, 0) == 0), key=len, reverse=True)[:10]

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

    # Stage 47 — when the base holds too little, go and find out instead of reporting a
    # dead end. The old recon could only say "add sources"; the swarm can now search its
    # own subject, read what it finds and file it, so the next pass has something to
    # work with. Everything read enters the ordinary knowledge pipeline, so this makes
    # the whole swarm better informed, not just this one mission.
    searched = prior_search
    if filed < RECON_SEARCH_BELOW and search_web:
        searched = _search_the_subject(db, mission, subjects, places, layers)
        if searched.get("filed"):
            # Re-read the base now that it has more in it — and carry the search report
            # through, or the operator would see the second pass with no sign that a
            # search ever happened.
            return run_recon(db, mission_id, layers=layers, search_web=False,
                             prior_search=searched)

    if filed:
        reason = ""
    elif missing:
        reason = ("В базе знаний нет материала по словам темы: " + ", ".join(missing)
                  + (". Поиск в интернете тоже ничего не дал — проверьте формулировку темы "
                     "или доступность поиска." if searched is not None
                     else ". Добавьте источники по теме — иначе рой будет спорить без фактов."))
    else:
        reason = (f"Ни один материал не назвал {RECON_MIN_SUBJECTS} слов темы "
                  f"(лучший — {best}) — тема описана слишком общо.")
    return {"scanned": len(rows), "admitted": len(admitted), "filed": filed,
            "best_score": best, "subject_terms": subjects,
            "missing_terms": missing, "searched": searched, "reason": reason}


def mission_places(db: Session, mission: Mission) -> list[str]:
    """
    Where this mission is about, taken from the channels it works.

    A mission states its subject but rarely its geography — #10 says «городу нужен
    развитый общественный транспорт» and never names the city. Searching that verbatim
    brought back Putin on Russian public transport and a Google Play page for the
    Moscow metro app: true, findable, and useless to a Tashkent channel. The place the
    mission actually operates in is already known — its targets are profiled.
    """
    from app.models import ChannelProfile, MissionTarget

    idents = [t.identifier for t in
              db.query(MissionTarget)
              .filter(MissionTarget.mission_id == mission.id,
                      MissionTarget.status == "active").all()]
    if not idents:
        return []
    rows = (db.query(ChannelProfile)
            .filter(ChannelProfile.channel_ref.in_(idents)).all())
    places: list[str] = []
    for r in rows:
        for part in (r.geo_label or "").split(","):
            p = part.strip().lower()
            if p and p not in places:
                places.append(p)
    return places[:2]


def _search_the_subject(db: Session, mission: Mission, subjects: list[str],
                        places: list[str], layers: list[str] | None) -> dict[str, Any]:
    """Search the web for the mission's subject, in its own place, and file the result."""
    from app import tools

    where = " ".join(places[:1])
    queries = []
    subj = " ".join(subjects[:4])
    if subj.strip():
        queries.append(f"{where} {subj}".strip())
    if mission.title:
        queries.append(tools.clean_query(f"{where} {mission.title}".strip()))
    total_filed, found = 0, 0
    for q in queries[:2]:
        report = tools.lookup(db, q, layers=layers or ["global"], recent=True)
        total_filed += int(report.get("filed") or 0)
        found += len(report.get("findings") or [])
    logger.info("Mission %s recon-search %s: %d finding(s), %d filed",
                mission.id, queries[:2], found, total_filed)
    return {"queries": queries[:2], "found": found, "filed": total_filed}
