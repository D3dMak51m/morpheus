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


def normalize(s: str) -> str:
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


def content_words(norm: str) -> set:
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

# Scripts this swarm never legitimately mixes INTO a comment. Latin is deliberately
# absent: brand names, handles and the odd "ok" inside Russian text are normal human
# writing. A comment written wholly in one of these is fine too (the persona answers a
# post in the post's own language) — what is not fine is a stray run of one inside a
# comment written in another, which is the model leaking its training data mid-sentence
# (`批评или ошибка`) and passing every check we had.
_ALIEN_SCRIPTS = {
    "CJK": (0x4E00, 0x9FFF),
    "kana": (0x3040, 0x30FF),
    "hangul": (0xAC00, 0xD7AF),
    "arabic": (0x0600, 0x06FF),
    "hebrew": (0x0590, 0x05FF),
    "devanagari": (0x0900, 0x097F),
    "thai": (0x0E00, 0x0E7F),
}


def _script_bleed(text: str) -> str:
    """The name of a foreign script bleeding into a comment written in another, or ""."""
    counts = {name: 0 for name in _ALIEN_SCRIPTS}
    familiar = 0
    for ch in text or "":
        code = ord(ch)
        if ("а" <= ch.lower() <= "я") or ch.lower() == "ё" or ("a" <= ch.lower() <= "z"):
            familiar += 1
            continue
        for name, (lo, hi) in _ALIEN_SCRIPTS.items():
            if lo <= code <= hi:
                counts[name] += 1
                break
    alien = sum(counts.values())
    if not alien:
        return ""
    # Written in that script → legitimate. Sprinkled into Cyrillic/Latin → bleed.
    if alien > familiar:
        return ""
    return max(counts, key=counts.get)


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

        # 7. Foreign script bleeding into the comment (Stage 46). qwen2.5:3b drops runs
        # of its training data mid-sentence — a published comment carried «批评или
        # ошибка» and passed every check above, because nothing here had ever looked at
        # the alphabet the words were written in.
        bleed = _script_bleed(text)
        if bleed:
            return False, f"Foreign script ({bleed}) bleeding into the comment."

        return True, "Valid"

    def clean_output(self, text: str) -> str:
        """Post-process a validated comment: strip garbage hashtags qwen2.5:3b likes to
        tack on — long glued transliteration gibberish like '#УЗБЕКИСТАНишегизилуенет'
        (an uppercase run welded to a lowercase nonsense tail, or an over-long tag).
        Normal short/CamelCase hashtags are kept. Sanitising (vs rejecting) avoids an
        extra regen for an otherwise good comment."""
        if not text:
            return text

        def _is_garbage(tok: str) -> bool:
            body = tok[1:].strip(".,!?;:)(»«\"'…")
            if len(body) >= 15:
                return True
            # Uppercase run (>=3) welded directly to a lowercase run (>=5) = gibberish.
            return bool(re.search(r"[A-ZА-ЯЁ]{3,}[a-zа-яё]{5,}", body))

        cleaned = re.sub(r"#[^\s#]+", lambda m: "" if _is_garbage(m.group(0)) else m.group(0), text)
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
        return cleaned or text

    def is_echo(self, text: str, references: List[str]) -> bool:
        """
        Detect when the model parroted the input back instead of replying. A weak
        LLM (e.g. qwen2.5:3b) frequently copies the incoming message verbatim or
        prefixes its reply with it — which instantly exposes the bot. Returns True
        if ``text`` is a near-duplicate of, starts by copying, or fully contains any
        reference (the human's message / the post).
        """
        t = normalize(text)
        if not t:
            return False
        for ref in references or []:
            r = normalize(ref)
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
            ref_cw = content_words(r)
            if len(ref_cw) >= 4:
                shared = ref_cw & content_words(t)
                if len(shared) / len(ref_cw) >= 0.6:
                    return True
        return False

    def is_repeat(self, text: str, history: List[str]) -> bool:
        """
        Detect when the agent is repeating *itself* — the same opening, phrasing, or
        talking points it already used in a recent comment (the thing that makes the
        bot read as a robot reciting a script). Compares the new text to the agent's
        own recent outputs: a high whole-text similarity OR a high two-way overlap of
        meaningful words (same talking points, even if reworded) counts as a repeat.
        """
        t = normalize(text)
        if not t:
            return False
        t_cw = content_words(t)
        for prev in history or []:
            p = normalize(prev)
            if len(p) < 12:
                continue
            if SequenceMatcher(None, t, p).ratio() > 0.6:
                return True
            # Same opening as a recent comment (~first 4-5 words) — a classic bot
            # tell ("Как часто я тоскую…", "А если бы были…"). Force a new lead-in.
            if len(t) >= 18 and t[:18] == p[:18]:
                return True
            p_cw = content_words(p)
            if len(t_cw) >= 4 and len(p_cw) >= 4:
                shared = t_cw & p_cw
                # min() so reusing most of either comment's key words trips it —
                # catches recycled talking points regardless of length differences.
                # 0.7 (not 0.6): on a narrow-topic mission two genuinely different
                # comments still share topic words, so only a strong overlap (clear
                # rehash) should block — the prompt is the primary anti-repeat lever.
                if len(shared) / min(len(t_cw), len(p_cw)) >= 0.7:
                    return True
        return False
