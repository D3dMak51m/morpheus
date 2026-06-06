"""
ORPHEUS — Guardrails Validator
================================
Validates synthesized LLM outputs to ensure they are free of AI markers,
meet length constraints, and match the persona's style.
"""

import logging
import re
from typing import Tuple

logger = logging.getLogger("orpheus.guardrails")

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
