import re
from typing import Optional

# ── Geographic Mapping ────────────────────────────────────────────────────
# Keywords mapping to layers. Lowercased for matching.

REGION_KEYWORDS = ["central asia", "центральная азия", "средняя азия"]
STATE_KEYWORDS = ["uzbekistan", "узбекистан", "uzb", "ўзбекистон"]
CITY_KEYWORDS = ["tashkent", "ташкент", "toshkent", "yunusabad", "юнусабад", "chorsu", "чорсу", "chilanzar", "чиланзар", "mirzo ulugbek", "мирзо улугбек"]

def classify_layers(text_content: str, source_metadata: Optional[dict] = None) -> dict:
    """
    Assign geographic and thematic layers to a piece of content.
    Layers: Global, Region (Central Asia), State, City, Personal.
    
    Implements the 5-layer classification using local geographic keywords.
    """
    layers = {
        "global": True,  # Everything is global by default
        "region": None,
        "state": None,
        "city": None,
        "personal_tags": [],
    }

    if source_metadata:
        if source_metadata.get("state"):
            layers["state"] = source_metadata.get("state")
        if source_metadata.get("city"):
            layers["city"] = source_metadata.get("city")
        if source_metadata.get("personal_tags"):
            layers["personal_tags"] = source_metadata.get("personal_tags", [])

    text_lower = text_content.lower()

    # Rule-based classification for text
    if any(keyword in text_lower for keyword in REGION_KEYWORDS):
        layers["region"] = "Central Asia"
    
    if any(keyword in text_lower for keyword in STATE_KEYWORDS):
        layers["region"] = "Central Asia"  # State implies region
        layers["state"] = "Uzbekistan"

    if any(keyword in text_lower for keyword in CITY_KEYWORDS):
        layers["region"] = "Central Asia"
        layers["state"] = "Uzbekistan"
        
        # Determine specific city/district if possible
        if "tashkent" in text_lower or "ташкент" in text_lower or "toshkent" in text_lower:
            layers["city"] = "Tashkent"
        else:
            layers["city"] = "Tashkent" # Defaulting for the specified districts
            
        if "yunusabad" in text_lower or "юнусабад" in text_lower:
            layers["personal_tags"].append("yunusabad")
        if "chorsu" in text_lower or "чорсу" in text_lower:
            layers["personal_tags"].append("chorsu")
            
    # Thematic tags based on simple NLP
    if any(k in text_lower for k in ["traffic", "пробки", "дороги", "transport", "транспорт"]):
        layers["personal_tags"].append("infrastructure")
    if any(k in text_lower for k in ["power", "свет", "electricity", "электричество", "отключение"]):
        layers["personal_tags"].append("utilities")

    # Deduplicate tags
    layers["personal_tags"] = list(set(layers["personal_tags"]))

    return layers
