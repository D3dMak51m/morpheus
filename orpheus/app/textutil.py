"""
ORPHEUS — Input hygiene for judging and prompting
==================================================
Stage 38. Real Telegram posts are not clean sentences: they carry promo tails
("Наш канал в MAX", "Подписывайтесь"), tracking links, emoji runs, and — worst —
OCR dumps of TV schedules and posters ("25 ИЮЛЯ 13:55 КОММЕНТАТОРЫ: …"), which is
what MYRMIDON's media reader produces for text-card images.

Measured on the live stand: feeding that raw text to the relevance gate made
`qwen2.5:3b` answer "нет" 50 times out of 50, while the same prompt over the
cleaned/short version answered correctly. A weak model drowns in noise long before
it reasons — so cleaning the input is not cosmetics, it is the gate's precondition.

Everything here is conservative: we strip only what is provably boilerplate, never
the substance of the post.
"""

import re
from typing import Optional

# Promotional / navigational boilerplate channels append to almost every post.
_PROMO_PATTERNS = [
    r"наш канал в \w+",
    r"подпи[сш][ыи]\w*",
    r"подключай\w*",
    r"читайте (?:нас|также)",
    r"больше новостей",
    r"источник:?\s*\S+$",
    r"реклама\.?\s*(?:erid|ерид)[:\s]\S+",
    r"erid[:\s]\S+",
    r"@[A-Za-z0-9_]{4,}\s*$",          # trailing channel handle
    r"#\w+(?:\s+#\w+)+\s*$",           # trailing hashtag block
]
_PROMO_RE = re.compile("|".join(_PROMO_PATTERNS), re.IGNORECASE | re.MULTILINE)

_TAG_RE = re.compile(r"<[^>]+>")
_PREVIEW_ATTR_RE = re.compile(r"(?:alt\s*=\s*[\"']?Preview[\"']?|src\s*=\s*[\"'][^\"']*[\"'])",
                              re.IGNORECASE)
_URL_RE = re.compile(r"https?://\S+|t\.me/\S+", re.IGNORECASE)
_EMOJI_RUN_RE = re.compile(
    r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF←-⇿⬀-⯿]+"
)
_WS_RE = re.compile(r"[ \t ]+")
_MULTI_NL_RE = re.compile(r"\n{3,}")

# A "schedule dump": OCR of a TV programme / poster. Recognised by a high density of
# clock times and ALL-CAPS name lists; these carry no discussable content at all.
_TIME_RE = re.compile(r"\b\d{1,2}[:.]\d{2}\b")
_CAPS_WORD_RE = re.compile(r"\b[А-ЯЁA-Z]{4,}\b")


def is_schedule_dump(text: str) -> bool:
    """
    True for OCR'd broadcast schedules / posters — nothing to discuss there.

    Signature: clock times next to a pile of ALL-CAPS names ("25 ИЮЛЯ 13:55
    КОММЕНТАТОРЫ: АЛЕКСАНДР НЕЦЕНКО …"). Thresholds are set from the real dumps the
    media reader produced on @Match_TV; deliberately not stricter, because letting one
    through costs a wasted LLM call and a nonsense comment.
    """
    if not text or len(text) < 80:
        return False
    times = len(_TIME_RE.findall(text))
    caps = len(_CAPS_WORD_RE.findall(text))
    words = max(len(text.split()), 1)
    return (times >= 2 and caps >= 5) or (times >= 1 and caps / words > 0.5)


def clean_post_text(text: str, keep_urls: bool = False, max_len: int = 700) -> str:
    """
    Turn a raw post into the substance a human would actually react to.

    Removes promo tails, links and decorative emoji runs, collapses whitespace and
    truncates on a sentence boundary. Never returns more than ``max_len`` chars —
    a 3B model reasons over a paragraph, not over a wall.
    """
    s = (text or "").strip()
    if not s:
        return ""
    # Markup never belongs in a prompt: it is noise the model may echo. Facts stored
    # before Stage 38 still carry `<img … Preview src=…>` tails.
    s = _TAG_RE.sub(" ", s)
    s = _PREVIEW_ATTR_RE.sub(" ", s)
    if not keep_urls:
        s = _URL_RE.sub(" ", s)
    s = _PROMO_RE.sub(" ", s)
    s = _EMOJI_RUN_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s)
    s = _MULTI_NL_RE.sub("\n\n", s)
    s = "\n".join(line.strip() for line in s.split("\n"))
    s = s.strip()
    if len(s) <= max_len:
        return s
    cut = s[:max_len]
    # Prefer to end on a sentence, else on a word.
    for sep in (". ", "! ", "? ", "\n"):
        idx = cut.rfind(sep)
        if idx > max_len * 0.5:
            return cut[: idx + 1].strip()
    idx = cut.rfind(" ")
    return (cut[:idx] if idx > 0 else cut).strip() + "…"


def describe_media(media_context: str, limit: int = 220) -> str:
    """Compact one-line rendering of what the post's photos/audio contain."""
    s = (media_context or "").strip()
    if not s:
        return ""
    s = _WS_RE.sub(" ", s.replace("\n", " · "))
    return s[:limit]


def judging_text(post_text: str, media_context: str = "") -> str:
    """
    The exact text the relevance gate should judge: cleaned post plus, when the post
    is media-dominant, a short description of what is in the media. Returns "" when
    there is provably nothing to discuss (empty, or a schedule dump).
    """
    body = clean_post_text(post_text)
    media = describe_media(media_context)
    if is_schedule_dump(body) or (not body and is_schedule_dump(media)):
        # Keep only the media gist — a schedule OCR has no discussable substance.
        body = ""
    parts = [p for p in (body, media) if p]
    return "\n".join(parts).strip()


# ── Mission vocabulary ────────────────────────────────────────────────────

# Generic words that carry no topical signal. Kept deliberately broad: every one of
# these previously ended up in the "или упоминает: …" hint and in the keyword
# recall-override, where they matched unrelated posts ("должен", "выиграть").
STOPWORDS = {
    "поддерживать", "поддержка", "поддержки", "против", "продвигать", "продвижение",
    "развитие", "развития", "развитой", "удобный", "удобного", "системно", "системный",
    "решаются", "решать", "проблема", "проблемы", "нужно", "важно", "нельзя", "также",
    "всегда", "будет", "более", "менее", "очень", "может", "чтобы", "потому", "вместе",
    "целью", "цель", "миссия", "миссии", "наша", "наши", "сторонник", "позиция",
    "должен", "должна", "должны", "выиграть", "проиграл", "проиграть", "сильная",
    "лучший", "лучшая", "лучше", "хуже", "никто", "никогда", "честно", "честная",
    "команда", "нашей", "нашего", "любой", "каждый", "просто", "сейчас", "нужен",
    "стоит", "здесь", "тогда", "после", "перед", "через", "сколько", "почему",
    "который", "которые", "которая", "этого", "этому", "этот", "была", "были",
    "支持",
}

_TOKEN_RE = re.compile(r"[а-яёa-z]{4,}", re.IGNORECASE)


def keywords(*texts: Optional[str], limit: int = 10, min_len: int = 5) -> list[str]:
    """
    Topical anchor words of a mission (its subject, actors, adversaries).

    Used to ground the gate's question and as a recall-override. Generic vocabulary
    is stripped, so a post no longer counts as "on topic" merely because it contains
    the word «должен».
    """
    seen: list[str] = []
    for text in texts:
        for tok in _TOKEN_RE.findall((text or "").lower()):
            if len(tok) < min_len or tok in STOPWORDS or tok in seen:
                continue
            seen.append(tok)
            if len(seen) >= limit:
                return seen
    return seen


def stem(word: str, size: int = 6) -> str:
    """Crude declension-tolerant stem. 6 chars (was 5) — «долже» used to match «должен»
    inside unrelated words and hand the gate a false positive."""
    return (word or "")[:size]


def keyword_hit(text: str, words: list[str], min_hits: int = 1) -> bool:
    """Does the text mention at least ``min_hits`` of the mission's anchor words?"""
    blob = (text or "").lower()
    hits = 0
    for w in words:
        s = stem(w)
        if len(s) >= 5 and s in blob:
            hits += 1
            if hits >= min_hits:
                return True
    return False
