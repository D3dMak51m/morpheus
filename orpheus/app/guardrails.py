"""
ORPHEUS — Guardrails Validator
================================
Validates synthesized LLM outputs to ensure they are free of AI markers,
meet length constraints, and match the persona's style.
"""

import logging
import re
from difflib import SequenceMatcher
from typing import Tuple, List

logger = logging.getLogger("orpheus.guardrails")


def _normalize(s: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace — for echo comparison."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (s or "").lower())).strip()


# Frequent function words that inflate overlap without indicating an echo.
_STOPWORDS = {
    "а", "и", "в", "во", "на", "по", "за", "у", "к", "с", "со", "о", "об", "от",
    "до", "из", "не", "ни", "но", "да", "же", "ли", "бы", "то", "это", "что",
    "как", "так", "вот", "уже", "ещё", "еще", "для", "при", "про", "под", "над",
    "вы", "ты", "я", "мы", "он", "она", "они", "вас", "вам", "мне", "тебя", "его",
    "the", "a", "an", "to", "of", "in", "on", "for", "and", "or", "is", "are",
    "you", "i", "we", "it", "this", "that", "my", "your",
}


def _content_words(norm: str) -> set:
    """Content tokens (length>2, not a stopword, not pure digits) for overlap."""
    return {w for w in norm.split() if len(w) > 2 and w not in _STOPWORDS and not w.isdigit()}

# Common AI phrasing markers that ruin the illusion of a human persona
AI_MARKERS = [
    r"as an ai",
    r"i am an ai",
    r"as a language model",
    r"в качестве искусственного интеллекта",
    r"как ии",
    r"я искусственный интеллект",
    r"я языковая модель",
    r"however, it is important to note",
    r"it's important to note",
    r"важно отметить, что",
    r"я не могу",
    r"i cannot",
    r"i apologize",
    r"приношу свои извинения",
    r"as a helpful assistant",
    r"как полезный помощник"
]

# Marketing / Spam markers
SPAM_MARKERS = [
    r"click here",
    r"нажми сюда",
    r"переходи по ссылке",
    r"купить сейчас",
    r"subscribe to",
    r"подписывайтесь на канал"
]

class OutputGuardrails:
    def __init__(self):
        self.ai_patterns = [re.compile(marker, re.IGNORECASE) for marker in AI_MARKERS]
        self.spam_patterns = [re.compile(marker, re.IGNORECASE) for marker in SPAM_MARKERS]

    def validate_output(self, text: str) -> Tuple[bool, str]:
        """
        Validates the text against strict rules.
        Returns (is_valid, reason_if_invalid).
        """
        if not text or not text.strip():
            return False, "Output is empty."

        text_lower = text.lower()

        # 1. Length bounds
        if len(text) < 10:
            return False, "Output is too short (less than 10 characters)."
        
        if len(text) > 2000:
            return False, "Output is too long (exceeds 2000 characters)."

        # 2. Check for AI markers
        for pattern in self.ai_patterns:
            if pattern.search(text_lower):
                return False, f"Detected AI marker: {pattern.pattern}"

        # 3. Check for spam/marketing markers
        for pattern in self.spam_patterns:
            if pattern.search(text_lower):
                return False, f"Detected spam/marketing marker: {pattern.pattern}"

        # 4. Check for excessive repetition (e.g., LLM looping)
        words = text_lower.split()
        if len(words) > 20:
            # Check if any 3-word phrase repeats more than 3 times
            phrases = [" ".join(words[i:i+3]) for i in range(len(words)-2)]
            for phrase in set(phrases):
                if phrases.count(phrase) > 3:
                    return False, "Output contains excessive repetition."

        # 5. Check for formatting breaks (raw JSON, code blocks)
        if "```" in text:
            return False, "Output contains markdown code blocks."
        if text.strip().startswith("{") and text.strip().endswith("}"):
            return False, "Output leaked raw JSON format."

        # 6. Check for Character Slip markers
        slip_markers = [
            "as a persona", "i am roleplaying", "here is my response", "вот мой ответ"
        ]
        for marker in slip_markers:
            if marker in text_lower:
                return False, f"Character slip detected: {marker}"

        return True, "Valid"

    def is_echo(self, text: str, references: List[str]) -> bool:
        """
        Detect when the model parroted the input back instead of replying. A weak
        LLM (e.g. qwen2.5:3b) frequently copies the incoming message verbatim or
        prefixes its reply with it — which instantly exposes the bot. Returns True
        if ``text`` is a near-duplicate of, starts by copying, or fully contains any
        reference (the human's message / the post).
        """
        t = _normalize(text)
        if not t:
            return False
        for ref in references or []:
            r = _normalize(ref)
            if len(r) < 8:
                continue
            # Whole-message near-duplicate.
            if SequenceMatcher(None, t, r).ratio() > 0.7:
                return True
            # Reply begins by copying the reference.
            prefix = r[: max(12, int(len(r) * 0.6))]
            if prefix and t.startswith(prefix):
                return True
            # The reference appears verbatim inside the reply.
            if len(r) >= 15 and r in t:
                return True
            # High content-word overlap (robust to inflection / word order): the
            # reply reuses most of the reference's meaningful words → it's parroting.
            ref_cw = _content_words(r)
            if len(ref_cw) >= 4:
                shared = ref_cw & _content_words(t)
                if len(shared) / len(ref_cw) >= 0.6:
                    return True
        return False
