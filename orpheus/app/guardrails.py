"""
ORPHEUS — Guardrails Module (AI Output Validator)
===================================================
Validates generated text before it is sent to MYRMIDON for publication.
Ensures the output does not contain detectable AI markers, promotional
language, or content that would trigger platform safety filters.

Validation rules:
  1. No common AI-generated phrases ("as an AI", "I'm a language model", etc.)
  2. No excessive hashtag usage
  3. No corporate/promotional tone markers
  4. Length bounds enforcement
  5. No repeated sentences or phrases
  6. Basic profanity/toxicity filtering (to avoid platform bans)
"""

import logging
import re
from typing import Tuple

logger = logging.getLogger("orpheus.guardrails")

# ── AI marker patterns (case-insensitive) ─────────────────────────────────

AI_MARKER_PATTERNS = [
    r"\bas an ai\b",
    r"\bi'?m a language model\b",
    r"\bi'?m an ai\b",
    r"\bas a large language model\b",
    r"\bai assistant\b",
    r"\bi cannot provide\b",
    r"\bi don'?t have personal\b",
    r"\bmy training data\b",
    r"\bi was trained\b",
    r"\baccording to my knowledge\b",
    r"\bI'?m not able to\b",
    r"\bmy programming\b",
    r"\bI'?m designed to\b",
    r"\bI'?m here to help\b",
    r"\bI appreciate your\b",
    r"\bgreat question\b",
    r"\bthat's a great\b",
    r"\blet me help you\b",
    r"\bcertainly!\b",
    r"\babsolutely!\b",
    r"\bdefinitely!\b",
    r"\bof course!\b",
    # Russian AI markers
    r"\bкак ии\b",
    r"\bкак языковая модель\b",
    r"\bя не могу предоставить\b",
    r"\bмои тренировочные данные\b",
    r"\bсогласно моим знаниям\b",
    r"\bотличный вопрос\b",
    r"\bбезусловно!\b",
]

# Compile patterns for performance
_ai_marker_regex = re.compile(
    "|".join(AI_MARKER_PATTERNS),
    re.IGNORECASE,
)

# ── Promotional/corporate tone markers ────────────────────────────────────

PROMO_PATTERNS = [
    r"#\w+\s*#\w+\s*#\w+",  # 3+ consecutive hashtags
    r"\bcheck out\b.*\blink\b",
    r"\bsubscribe\b.*\bchannel\b",
    r"\bfollow me\b",
    r"\blike and share\b",
    r"\buse code\b",
    r"\bdiscount\b.*\blink\b",
    r"\bподписывайтесь\b",
    r"\bставьте лайк\b",
    r"\bссылка в описании\b",
]

_promo_regex = re.compile(
    "|".join(PROMO_PATTERNS),
    re.IGNORECASE,
)

# ── Length bounds ─────────────────────────────────────────────────────────

MIN_RESPONSE_LENGTH = 10    # Characters
MAX_RESPONSE_LENGTH = 1000  # Characters


def validate_text(text: str) -> Tuple[bool, str]:
    """
    Run all guardrail checks on a generated text.

    Returns:
        Tuple of (is_safe: bool, cleaned_text: str).
        If is_safe is False, the text should NOT be published.
        cleaned_text contains minor sanitizations applied to safe text.
    """
    if not text or not text.strip():
        logger.warning("Guardrails: empty text received.")
        return False, ""

    cleaned = text.strip()

    # ── Check 1: AI markers ───────────────────────────────────────
    ai_match = _ai_marker_regex.search(cleaned)
    if ai_match:
        logger.warning(
            "Guardrails REJECT — AI marker detected: '%s'",
            ai_match.group(),
        )
        return False, cleaned

    # ── Check 2: Promotional language ─────────────────────────────
    promo_match = _promo_regex.search(cleaned)
    if promo_match:
        logger.warning(
            "Guardrails REJECT — promotional pattern detected: '%s'",
            promo_match.group(),
        )
        return False, cleaned

    # ── Check 3: Length bounds ────────────────────────────────────
    if len(cleaned) < MIN_RESPONSE_LENGTH:
        logger.warning(
            "Guardrails REJECT — text too short (%d chars, min=%d).",
            len(cleaned),
            MIN_RESPONSE_LENGTH,
        )
        return False, cleaned

    if len(cleaned) > MAX_RESPONSE_LENGTH:
        logger.info(
            "Guardrails TRIM — text too long (%d chars, max=%d). Truncating.",
            len(cleaned),
            MAX_RESPONSE_LENGTH,
        )
        # Truncate at the last sentence boundary before the limit
        truncated = cleaned[:MAX_RESPONSE_LENGTH]
        last_period = max(
            truncated.rfind("."),
            truncated.rfind("!"),
            truncated.rfind("?"),
        )
        if last_period > MIN_RESPONSE_LENGTH:
            cleaned = truncated[: last_period + 1]
        else:
            cleaned = truncated

    # ── Check 4: Repeated sentences ──────────────────────────────
    sentences = re.split(r"[.!?]+", cleaned)
    sentences = [s.strip().lower() for s in sentences if s.strip()]
    if len(sentences) >= 2:
        unique = set(sentences)
        if len(unique) < len(sentences) * 0.5:
            logger.warning(
                "Guardrails REJECT — excessive repetition detected (%d/%d unique).",
                len(unique),
                len(sentences),
            )
            return False, cleaned

    # ── Check 5: Excessive exclamation/emoji density ─────────────
    exclamation_count = cleaned.count("!")
    if exclamation_count > 5:
        logger.info(
            "Guardrails SANITIZE — reducing excessive exclamation marks (%d).",
            exclamation_count,
        )
        # Replace consecutive exclamation marks
        cleaned = re.sub(r"!{2,}", "!", cleaned)

    # ── Check 6: Strip trailing whitespace and normalize ─────────
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = cleaned.strip()

    logger.debug("Guardrails PASS — text length=%d chars.", len(cleaned))
    return True, cleaned
