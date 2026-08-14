"""
DAEDALUS — Canonical place vocabulary (Stage 39)
==================================================
The knowledge base is tagged by an LLM in whatever language the source happened to
use, so the SAME place arrived under several spellings and none of them matched:

    tag 'ташкент'    →  0 facts        tag 'узбекистан'  →   1 fact
    tag 'tashkent'   →  1 fact         tag 'uzbekistan'  →  24 facts

`/knowledge/internal/by-geo` grounds a channel's comments in its own region's news by
matching the channel profile's `geo_label` ("ташкент, узбекистан" — Russian, because
the profiler writes Russian) against those tags. With the corpus tagged in English the
overlap was empty, and the only fact that ever came back did so through a merged
tag-soup: a Lavrov/Ukraine story that had absorbed the word `узбекистан`.

So places are canonicalised on the way IN (stored in `KnowledgeFact.geo_tags`) and on
the way OUT (query terms), both through this table. Canonical form is the lowercase
Russian name — that is what the profiler, the missions and the operator UI speak.

Coverage is deliberately corpus-driven: Uzbekistan in depth (the swarm's actual
operating region, incl. Uzbek Latin and Cyrillic spellings), then Central Asia,
Russia/CIS and the world actors that show up in the feeds. Unknown places are kept
verbatim rather than dropped — an unmapped place still matches itself.
"""

import re
from typing import Iterable

# canonical (lowercase Russian) → every spelling seen in the wild.
# Uzbek appears in both scripts: latin (Toshkent) and cyrillic (Тошкент).
_PLACE_ALIASES: dict[str, tuple[str, ...]] = {
    # ── Uzbekistan: country, capital, regions, major cities ────────────────
    "узбекистан": ("uzbekistan", "oʻzbekiston", "o'zbekiston", "ozbekiston",
                   "узбекистон", "republic of uzbekistan", "узб"),
    "ташкент": ("tashkent", "toshkent", "тошкент", "ташкентская область",
                "toshkent viloyati", "ташкентская обл"),
    "самарканд": ("samarkand", "samarqand", "самарқанд"),
    "бухара": ("bukhara", "buxoro", "бухоро"),
    "андижан": ("andijan", "andijon", "андижон"),
    "фергана": ("fergana", "ferghana", "farg'ona", "fargona", "фарғона", "ферганская долина"),
    "наманган": ("namangan",),
    "хорезм": ("khorezm", "xorazm", "хоразм", "ургенч", "urgench", "urganch"),
    "каракалпакстан": ("karakalpakstan", "qoraqalpogʻiston", "qoraqalpogiston",
                       "қорақалпоғистон", "нукус", "nukus"),
    "сурхандарья": ("surkhandarya", "surxondaryo", "сурхондарё", "термез", "termez", "termiz"),
    "кашкадарья": ("kashkadarya", "qashqadaryo", "қашқадарё", "карши", "karshi", "qarshi"),
    "джизак": ("jizzakh", "jizzax", "жиззах"),
    "сырдарья": ("syrdarya", "sirdaryo", "сирдарё", "гулистан", "gulistan"),
    "навои": ("navoi", "navoiy", "навоий"),
    "ангрен": ("angren",),
    "чирчик": ("chirchik", "chirchiq"),

    # ── Central Asia ───────────────────────────────────────────────────────
    "казахстан": ("kazakhstan", "qozogʻiston", "қозоғистон", "астана", "astana",
                  "алматы", "almaty"),
    "киргизия": ("kyrgyzstan", "кыргызстан", "qirgʻiziston", "бишкек", "bishkek", "кыргыз"),
    "таджикистан": ("tajikistan", "tojikiston", "тожикистон", "душанбе", "dushanbe"),
    "туркменистан": ("turkmenistan", "turkmaniston", "ашхабад", "ashgabat"),
    "афганистан": ("afghanistan", "afgʻoniston", "кабул", "kabul"),
    "центральная азия": ("central asia", "markaziy osiyo", "средняя азия", "марказий осиё"),

    # ── Russia / CIS ───────────────────────────────────────────────────────
    "россия": ("russia", "rossiya", "russian federation", "рф", "россия́",
               "russiya", "русия"),
    "москва": ("moscow", "moskva"),
    "санкт-петербург": ("saint petersburg", "st petersburg", "спб", "петербург"),
    "украина": ("ukraine", "ukraina", "укра[и]на"),
    "киев": ("kyiv", "kiev"),
    "белоруссия": ("belarus", "беларусь", "минск", "minsk"),
    "азербайджан": ("azerbaijan", "ozarbayjon", "баку", "baku"),
    "армения": ("armenia", "ереван", "yerevan"),
    "грузия": ("georgia", "gruziya", "гурҷистон", "тбилиси", "tbilisi"),

    # ── World actors that recur in the feeds ───────────────────────────────
    "сша": ("usa", "us", "united states", "aqsh", "америка", "america", "u.s."),
    "китай": ("china", "xitoy", "хитой", "пекин", "beijing", "prc"),
    "евросоюз": ("eu", "european union", "европейский союз", "yevropa ittifoqi",
                 "европа иттифоқи", "брюссель"),
    "турция": ("turkey", "turkiye", "türkiye", "turkiya", "анкара", "ankara",
               "стамбул", "istanbul"),
    "иран": ("iran", "eron", "эрон", "тегеран", "tehran"),
    "израиль": ("israel", "isroil", "тель-авив"),
    "индия": ("india", "hindiston", "дели", "delhi"),
    "пакистан": ("pakistan",),
    "саудовская аравия": ("saudi arabia", "saudiya arabistoni", "эр-рияд"),
    "великобритания": ("uk", "united kingdom", "britain", "britaniya", "лондон", "london"),
    "германия": ("germany", "germaniya", "берлин", "berlin", "фрг"),
    "франция": ("france", "frantsiya", "париж", "paris"),
    "япония": ("japan", "yaponiya", "токио", "tokyo"),
    "южная корея": ("south korea", "korea", "сеул", "seoul"),
    "европа": ("europe", "yevropa"),
    "ближний восток": ("middle east", "yaqin sharq"),
}

# Reverse index: every spelling (incl. the canonical one) → canonical name.
_ALIAS_TO_CANON: dict[str, str] = {}
for _canon, _aliases in _PLACE_ALIASES.items():
    _ALIAS_TO_CANON[_canon] = _canon
    for _a in _aliases:
        _ALIAS_TO_CANON[_a] = _canon

_PUNCT_RE = re.compile(r"[«»\"'`.,;:!?()\[\]]+")
_WS_RE = re.compile(r"\s+")


def normalise_tag(tag: str) -> str:
    """
    Lowercase, de-punctuate and collapse a raw LLM tag into a comparable token.

    Also undoes the two shapes qwen2.5:3b likes to emit for multi-word tags —
    `парламентский_запрос` and `ministru_kultury` — so an underscored tag can still
    match its spaced twin.
    """
    t = (tag or "").strip().lower().replace("ё", "е")
    t = t.replace("_", " ")
    t = _PUNCT_RE.sub(" ", t)
    return _WS_RE.sub(" ", t).strip()


def canonical_place(tag: str) -> str | None:
    """
    Canonical Russian name for ``tag`` if it names a known place, else ``None``.

    Matching is exact on the normalised alias — substring matching would turn
    "Джорджия" into "грузия" and every mention of "усть-каменогорск" into a city
    it is not.
    """
    return _ALIAS_TO_CANON.get(normalise_tag(tag))


def canonical_places(tags: Iterable[str]) -> list[str]:
    """Canonical, de-duplicated place names among ``tags`` (order-preserving)."""
    out: list[str] = []
    for tag in tags or []:
        canon = canonical_place(tag)
        if canon and canon not in out:
            out.append(canon)
    return out


def _alias_pattern(alias: str) -> re.Pattern:
    """
    Word-anchored matcher for one spelling, tolerant of inflection.

    Russian declension rewrites the ENDING, not just appends to it — "россия" is not a
    prefix of "России" — so each word is cut back to a stem before being anchored.
    Adjectives lose two characters because their endings are two long
    ("саудовская" → саудовск…, which reaches "Саудовской Аравии"); shorter nouns lose
    one ("россия" → росси…, "китай" → кита…). Multi-word aliases are stemmed word by
    word, since both halves decline.

    Short aliases keep both boundaries: a bare "us" would otherwise match inside
    "Russia", and "eu" inside "Europe".
    """
    words = [w for w in alias.split() if w]
    if not words:
        return re.compile(r"(?!)")          # never matches
    if len(alias) < 4:
        return re.compile(rf"\b{re.escape(alias)}\b", re.IGNORECASE)
    stems = [re.escape(w[:-2] if len(w) >= 7 else w[:-1] if len(w) >= 5 else w)
             for w in words]
    return re.compile(r"\b" + r"\w*\s+".join(stems), re.IGNORECASE)


_ALIAS_PATTERNS: dict[str, list[re.Pattern]] = {
    canon: [_alias_pattern(a) for a in (canon, *aliases)]
    for canon, aliases in _PLACE_ALIASES.items()
}


def places_in_text(places: Iterable[str], text: str) -> list[str]:
    """
    Keep only those ``places`` that the text actually mentions.

    qwen2.5:3b invents geography: a story headlined "Горловка в Запорожской области
    полностью обесточена" came back classified with places узбекистан and ташкент, and
    that single hallucination is enough to surface a Ukraine story as "current news"
    for a Tashkent channel — precisely the failure geo tagging exists to prevent.

    A place the source never named is not evidence, so it is dropped. Same rule the
    channel profiler applies to hot themes with a zero mention count. Places outside
    the vocabulary are kept: we have no aliases to verify them with, and they are not
    the ones doing the damage.
    """
    haystack = (text or "").replace("ё", "е")
    kept: list[str] = []
    for place in places or []:
        patterns = _ALIAS_PATTERNS.get(place)
        if patterns is None or any(p.search(haystack) for p in patterns):
            if place not in kept:
                kept.append(place)
    return kept


def expand_query_terms(terms: Iterable[str]) -> list[str]:
    """
    Resolve caller-supplied place terms to canonical names for a geo lookup.

    A term that maps to a known place becomes its canonical name; an unknown term is
    kept as-is (normalised), so an unmapped place still matches facts tagged with it.
    """
    out: list[str] = []
    for raw in terms or []:
        term = normalise_tag(raw)
        if not term:
            continue
        canon = _ALIAS_TO_CANON.get(term, term)
        if canon not in out:
            out.append(canon)
    return out
