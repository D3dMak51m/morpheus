"""
HUGINN — Layer Router Module
==============================
Routes incoming content to the appropriate processing layer
based on geographic and thematic classification.

Layers (from tech_spec.md):
  - Global  — World-level events
  - Region  — Central Asia
  - State   — Country-level (e.g., Uzbekistan)
  - City    — City-level (e.g., Tashkent)
  - Personal — Agent interest tags
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("huginn.router")

# ── Geographic keyword mappings for heuristic classification ──────────────

REGION_KEYWORDS = {
    "central_asia": [
        "центральная азия", "central asia", "средняя азия",
        "узбекистан", "казахстан", "кыргызстан", "таджикистан", "туркменистан",
        "uzbekistan", "kazakhstan", "kyrgyzstan", "tajikistan", "turkmenistan",
    ],
}

STATE_KEYWORDS = {
    "Uzbekistan": [
        "узбекистан", "uzbekistan", "ташкент", "самарканд", "бухара",
        "наманган", "андижан", "фергана", "нукус", "карши",
    ],
    "Kazakhstan": [
        "казахстан", "kazakhstan", "алматы", "астана", "нур-султан",
        "шымкент", "караганда",
    ],
}

CITY_KEYWORDS = {
    "Tashkent": ["ташкент", "tashkent", "тошкент"],
    "Samarkand": ["самарканд", "samarkand"],
    "Bukhara": ["бухара", "bukhara", "бухоро"],
    "Almaty": ["алматы", "almaty"],
    "Astana": ["астана", "astana", "нур-султан"],
}


@dataclass
class LayerClassification:
    """Result of content layer classification."""
    is_global: bool = False
    region: Optional[str] = None
    state: Optional[str] = None
    city: Optional[str] = None
    personal_tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "global": self.is_global,
            "region": self.region,
            "state": self.state,
            "city": self.city,
            "personal_tags": self.personal_tags,
        }


def classify_content(
    text: str,
    source_hints: Optional[dict] = None,
    agent_interests: Optional[list[str]] = None,
) -> LayerClassification:
    """
    Classify a text snippet into geographic and thematic layers.

    Priority: City > State > Region > Global.
    If the text matches a city keyword, the state and region are
    inferred automatically.

    Args:
        text: The raw text content to classify.
        source_hints: Optional metadata hints (e.g., from the source channel).
        agent_interests: List of agent personal interest tags to match against.

    Returns:
        LayerClassification with populated fields.
    """
    result = LayerClassification()
    text_lower = text.lower()

    # Apply source hints first (highest confidence)
    if source_hints:
        result.state = source_hints.get("state")
        result.city = source_hints.get("city")
        result.personal_tags = source_hints.get("personal_tags", [])
        if result.city or result.state:
            result.region = "Central Asia"
            return result

    # City detection
    for city_name, keywords in CITY_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            result.city = city_name
            # Infer state from city
            for state_name, state_kws in STATE_KEYWORDS.items():
                if any(kw in text_lower for kw in state_kws):
                    result.state = state_name
                    break
            else:
                # Default state inference from known city-state mappings
                city_state_map = {
                    "Tashkent": "Uzbekistan",
                    "Samarkand": "Uzbekistan",
                    "Bukhara": "Uzbekistan",
                    "Almaty": "Kazakhstan",
                    "Astana": "Kazakhstan",
                }
                result.state = city_state_map.get(city_name)
            result.region = "Central Asia"
            break

    # State detection (if city wasn't found)
    if result.state is None:
        for state_name, keywords in STATE_KEYWORDS.items():
            if any(kw in text_lower for kw in keywords):
                result.state = state_name
                result.region = "Central Asia"
                break

    # Region detection
    if result.region is None:
        for region_name, keywords in REGION_KEYWORDS.items():
            if any(kw in text_lower for kw in keywords):
                result.region = "Central Asia"
                break

    # If nothing matched, classify as Global
    if result.region is None and result.state is None and result.city is None:
        result.is_global = True

    # Personal interest tag matching
    if agent_interests:
        for interest in agent_interests:
            if interest.lower() in text_lower:
                result.personal_tags.append(interest)

    logger.debug(
        "Layer classification result: global=%s, region=%s, state=%s, city=%s, tags=%s",
        result.is_global,
        result.region,
        result.state,
        result.city,
        result.personal_tags,
    )
    return result
