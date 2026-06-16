"""
DAEDALUS — Channel Profiler (LLM)
===================================
Builds a per-channel profile from a sample of its posts via the local Ollama LLM
(strict-JSON, like ``classifier.auto_classify_text``). Two calls:

  • profile_channel  — heavy: geo (same closed layer set as knowledge_facts),
    topics, tags, a 1-2 sentence summary, audience/tone, language.
  • extract_themes   — light: the 3-6 topics being discussed *right now*.

Fail-soft: on any LLM/parse error returns empty structures so the caller can skip
the update without crashing. See CHANNEL_PROFILING.md.
"""

import json
import logging
import os
from typing import Any

import httpx

from app.models import LANDSCAPE_LAYERS

logger = logging.getLogger("daedalus.channel_profiler")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
TEXT_MODEL_NAME = os.getenv("TEXT_MODEL_NAME", "qwen2.5:3b")
PROFILE_TIMEOUT_SEC = float(os.getenv("PROFILE_TIMEOUT_SEC", "90"))
MAX_TOPICS = 8
MAX_TAGS = 10
MAX_THEMES = 6

_PROFILE_SYSTEM = f"""You are an analyst profiling a social-media channel from a sample \
of its recent posts (and its title). Infer what the channel is about and WHERE it is.

Return ONLY a JSON object with EXACTLY these keys:
{{
  "geo_layers": [geographic scope strings],
  "geo_label": "short human geo label, e.g. 'Ташкент, Узбекистан' (or '')",
  "topics": [1-{MAX_TOPICS} main themes of the channel, short lowercase phrases],
  "tags": [1-{MAX_TAGS} salient entities/places/keywords, lowercase],
  "summary": "1-2 sentence characterization of the channel IN RUSSIAN",
  "audience_tone": "short audience + tone description in Russian",
  "language": "dominant language code(s), e.g. 'ru' or 'ru, uz'"
}}

RULES for "geo_layers" — choose every scope that applies, from this CLOSED set only:
  - "global"   : world / international, or no specific locality.
  - "regional" : a multi-country region (e.g. Central Asia).
  - "state"    : a single country / nation.
  - "city"     : a specific city or district.
  - "personal" : hyper-local / personal daily life.
Infer geo from the TITLE and the posts (e.g. a "Tashkent news" channel => ["state","city"],
geo_label "Ташкент, Узбекистан"). Never invent layer names outside the closed set.

Output strictly valid JSON, no prose, no markdown."""

_THEMES_SYSTEM = f"""You read recent posts from one social-media channel and report what \
is being discussed RIGHT NOW. Return ONLY a JSON object:
{{ "themes": [1-{MAX_THEMES} short lowercase topic phrases being discussed most] }}
Be concrete (e.g. "пробки", "отключения света", "новые автобусы"). Strict JSON only."""


def _coerce_str_list(value: Any) -> list[str]:
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


async def _ollama_json(system: str, prompt: str) -> dict:
    payload = {
        "model": TEXT_MODEL_NAME,
        "prompt": prompt,
        "system": system,
        "stream": False,
        "format": "json",
        "keep_alive": 0,
        "options": {"temperature": 0.2},
    }
    async with httpx.AsyncClient(timeout=PROFILE_TIMEOUT_SEC) as client:
        resp = await client.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload)
        resp.raise_for_status()
        raw = resp.json().get("response", "").strip()
    return json.loads(raw)


def _posts_blob(posts: list[str], limit_chars: int) -> str:
    lines, total = [], 0
    for p in posts:
        p = (p or "").strip()
        if not p:
            continue
        lines.append(f"- {p[:300]}")
        total += len(lines[-1])
        if total >= limit_chars:
            break
    return "\n".join(lines)


async def profile_channel(title: str, posts: list[str]) -> dict:
    """Heavy profile. Returns dict (empty-ish on failure)."""
    empty = {"geo_layers": [], "geo_label": "", "topics": [], "tags": [],
             "summary": "", "audience_tone": "", "language": ""}
    blob = _posts_blob(posts, 3500)
    if not blob and not (title or "").strip():
        return empty
    prompt = f"CHANNEL TITLE: {title or '(none)'}\n\nRECENT POSTS:\n{blob}\n\nReturn the JSON profile now."
    try:
        raw = await _ollama_json(_PROFILE_SYSTEM, prompt)
    except Exception as exc:
        logger.warning("Channel profile LLM call failed: %s", exc)
        return empty
    if not isinstance(raw, dict):
        return empty
    geo_layers = [l for l in _coerce_str_list(raw.get("geo_layers")) if l in LANDSCAPE_LAYERS]
    result = {
        "geo_layers": geo_layers,
        "geo_label": str(raw.get("geo_label") or "").strip()[:200],
        "topics": _coerce_str_list(raw.get("topics"))[:MAX_TOPICS],
        "tags": _coerce_str_list(raw.get("tags"))[:MAX_TAGS],
        "summary": str(raw.get("summary") or "").strip()[:600],
        "audience_tone": str(raw.get("audience_tone") or "").strip()[:200],
        "language": str(raw.get("language") or "").strip()[:40],
    }
    logger.info("Profiled channel: geo=%s topics=%s", result["geo_layers"], result["topics"])
    return result


async def extract_themes(posts: list[str]) -> list[dict]:
    """Light 'hot themes now' extraction → [{theme, count}] (count = posts mentioning it)."""
    blob = _posts_blob(posts, 2500)
    if not blob:
        return []
    prompt = f"RECENT POSTS:\n{blob}\n\nReturn the JSON now."
    try:
        raw = await _ollama_json(_THEMES_SYSTEM, prompt)
    except Exception as exc:
        logger.warning("Channel themes LLM call failed: %s", exc)
        return []
    themes = _coerce_str_list((raw or {}).get("themes"))[:MAX_THEMES]
    low_posts = [(p or "").lower() for p in posts]
    out = []
    for t in themes:
        # crude frequency: posts whose text contains the theme's first word
        key = t.split()[0] if t.split() else t
        count = sum(1 for p in low_posts if key and key in p)
        out.append({"theme": t, "count": count})
    return out
