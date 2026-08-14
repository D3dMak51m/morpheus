"""
DAEDALUS — LLM Auto-Classification Engine (Stage 22)
======================================================
Deep knowledge classification. When raw text arrives at the ingest endpoint,
DAEDALUS asks the local Ollama LLM (qwen2.5:3b) to analyse it and extract — in a
single strict-JSON call — the geographic `layers`, thematic `categories`, and
salient `tags`. This metadata is stored on the KnowledgeFact alongside the
vector embedding so MUNINN's memory is multi-dimensionally queryable.

The model is forced into JSON mode (`format: "json"`) and given a rigid schema;
the result is then sanitised so only valid LANDSCAPE_LAYERS survive and arrays
are de-duplicated. The function is fail-soft: on any LLM/parse error it returns
empty arrays so ingestion can still proceed (falling back to default_layers).
"""

import json
import logging
import os
from typing import Any

import httpx

from app import geo
from app.models import LANDSCAPE_LAYERS

logger = logging.getLogger("daedalus.classifier")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
TEXT_MODEL_NAME = os.getenv("TEXT_MODEL_NAME", "qwen2.5:3b")
CLASSIFY_TIMEOUT_SEC = float(os.getenv("CLASSIFY_TIMEOUT_SEC", "60"))
MAX_TAGS = 8
MAX_CATEGORIES = 5
MAX_PLACES = 6

# Strict instruction. The model must return ONLY a JSON object with these keys.
_SYSTEM_PROMPT = f"""You are a meticulous intelligence analyst for a geopolitical \
monitoring system. Analyse the NEWS TEXT and classify it.

Return ONLY a JSON object with EXACTLY these four keys:
{{
  "layers": [array of geographic scope strings],
  "categories": [array of 1-{MAX_CATEGORIES} broad thematic category strings],
  "tags": [array of 1-{MAX_TAGS} specific lowercase keyword/entity strings],
  "places": [array of 0-{MAX_PLACES} geographic names ONLY — countries, regions, cities]
}}

RULES for "layers" — choose every scope that applies, from this CLOSED set only:
  - "global"   : world / international affairs, or no specific locality.
  - "regional" : a multi-country region (e.g. Central Asia, the EU, the Gulf).
  - "state"    : a single country / nation.
  - "city"     : a specific city or district.
  - "personal" : individual daily-life / hyper-local / personal interest.
A text often spans several layers (e.g. a city event of national importance =>
["state","city"]). Never invent layer names outside the closed set.

"categories": broad themes such as "politics", "economy", "infrastructure",
"security", "society", "technology", "sports", "culture", "health".
"tags": concrete entities, places, people, or topics mentioned.
"places": ONLY geographic names — countries, regions, cities. No people, no
organisations, no topics. Write them in RUSSIAN (e.g. "узбекистан", "ташкент",
"россия"). Empty array if the text names no place.

Output strictly valid JSON, no prose, no markdown."""


def _coerce_str_list(value: Any) -> list[str]:
    """Coerce an arbitrary LLM value into a clean list of non-empty strings."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        s = str(item).strip().lower()
        if s and s not in out:
            out.append(s)
    return out


def _sanitise(raw: dict, text: str = "") -> dict:
    """
    Validate/normalise the raw LLM JSON into the strict output contract.

    Stage 39 — tags are normalised (qwen2.5:3b emits `парламентский_запрос` and
    `ministru_kultury` for the same shape of thing) and any tag naming a known place
    is rewritten to its canonical Russian name, so the corpus stops splitting one
    place across `tashkent`/`ташкент`/`toshkent`.

    `geo_tags` is harvested from BOTH the model's `places` key and its `tags` — a 3B
    model fills a new schema key only some of the time, but it does mention the place
    among the tags, and a place found either way is still a place.
    """
    layers = [l for l in _coerce_str_list(raw.get("layers")) if l in LANDSCAPE_LAYERS]
    categories = _coerce_str_list(raw.get("categories"))[:MAX_CATEGORIES]

    tags: list[str] = []
    for tag in _coerce_str_list(raw.get("tags")):
        canon = geo.canonical_place(tag) or geo.normalise_tag(tag)
        if canon and canon not in tags:
            tags.append(canon)
    tags = tags[:MAX_TAGS]

    # Verified against the source text: the model invents places (a Zaporizhzhia
    # blackout came back tagged узбекистан + ташкент), and one invented place is
    # enough to serve a Ukraine story to a Tashkent channel as local news.
    geo_tags = geo.places_in_text(
        geo.canonical_places(
            _coerce_str_list(raw.get("places")) + _coerce_str_list(raw.get("tags"))
        ),
        text,
    )[:MAX_PLACES]

    return {"layers": layers, "categories": categories, "tags": tags, "geo_tags": geo_tags}


def ask_llm_short(prompt: str, max_tokens: int = 60, temperature: float = 0.2) -> str:
    """
    One short, synchronous generation — for the small naming/deciding questions
    DAEDALUS asks (a mission's subject words, "are fresh data needed here").

    Deliberately plain text, never `format=json`: this model answers a list of words
    far more reliably than a schema, and the callers here parse a comma-separated line.
    Penalties are left off for the same reason short classification calls in ORPHEUS
    turn them off — they push the model off clean, short answers.
    """
    try:
        with httpx.Client(timeout=CLASSIFY_TIMEOUT_SEC) as client:
            resp = client.post(f"{OLLAMA_BASE_URL}/api/generate", json={
                "model": TEXT_MODEL_NAME,
                "prompt": prompt,
                "stream": False,
                "keep_alive": 0,
                "options": {"temperature": temperature, "num_predict": max_tokens},
            })
            resp.raise_for_status()
            return (resp.json().get("response") or "").strip()
    except Exception as exc:
        logger.warning("Short LLM call failed: %s", exc)
        return ""


async def auto_classify_text(text: str) -> dict:
    """
    Classify ``text`` via the local Ollama LLM into layers/categories/tags.

    Always returns a dict with those three keys (possibly empty on failure).
    """
    empty = {"layers": [], "categories": [], "tags": [], "geo_tags": []}
    text = (text or "").strip()
    if not text:
        return empty

    payload = {
        "model": TEXT_MODEL_NAME,
        "prompt": f"NEWS TEXT:\n{text[:4000]}\n\nReturn the JSON classification now.",
        "system": _SYSTEM_PROMPT,
        "stream": False,
        "format": "json",        # force valid JSON output
        "keep_alive": 0,          # unload after inference (VRAM hygiene)
        "options": {"temperature": 0.1},
    }

    try:
        async with httpx.AsyncClient(timeout=CLASSIFY_TIMEOUT_SEC) as client:
            resp = await client.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload)
            resp.raise_for_status()
            raw_response = resp.json().get("response", "").strip()
    except Exception as exc:
        logger.warning("Auto-classification LLM call failed: %s", exc)
        return empty

    try:
        parsed = json.loads(raw_response)
    except json.JSONDecodeError:
        logger.warning("LLM classification returned non-JSON: %r", raw_response[:200])
        return empty

    result = _sanitise(parsed if isinstance(parsed, dict) else {}, text)
    logger.info(
        "Auto-classified: layers=%s categories=%s tags=%s geo=%s",
        result["layers"], result["categories"], result["tags"], result["geo_tags"],
    )
    return result
