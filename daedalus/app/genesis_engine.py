"""
DAEDALUS — Soul Genesis Engine
==============================
Programmatic synthesis of agent profiles via local Ollama inference.
Accepts basic seed vectors and returns structured psychological configurations.
"""

import json
import logging
import os
import httpx
from typing import Dict, Any, Optional
from pydantic import BaseModel, ValidationError

logger = logging.getLogger("daedalus.genesis_engine")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")

class GenesisSeed(BaseModel):
    caste: str  # "alpha", "beta", "gamma"
    agent_id: str
    codename: str
    focus: str # e.g., "Urban planner, Samarkand"
    platforms: list[str]

# ── Synthesis Functions ────────────────────────────────────────────────

def generate_profile(seed: GenesisSeed) -> Dict[str, Any]:
    """
    Synthesize an agent profile based on caste and focus.
    """
    logger.info("Genesis Engine: Generating %s profile for %s (%s)", seed.caste, seed.agent_id, seed.codename)
    
    # Base structure with defaults
    profile = {
        "agent_id": seed.agent_id,
        "codename": seed.codename,
        "caste": seed.caste.lower(),
        "platforms": seed.platforms,
        "active_hours_start": 8,
        "active_hours_end": 22,
        "layers_affinity": {
            "global": 1.0,
            "region": 1.0,
            "state": 1.0,
            "city": 1.0,
            "personal": 1.0
        }
    }
    
    # Simple configurations for noise/bot castes
    if seed.caste.lower() in ["beta", "gamma"]:
        logger.info("Genesis Engine: Applying fast-path template for %s caste.", seed.caste)
        profile.update({
            "full_name": f"{seed.codename} ({seed.caste.capitalize()})",
            "profession": "Automated Swarm Agent",
            "communication_style": {
                "tone": "neutral",
                "vocab_level": "basic" if seed.caste == "gamma" else "intermediate",
                "emoji_frequency": "low"
            },
            "behavioral_rules": {
                "max_posts_per_hour": 15 if seed.caste == "gamma" else 5,
                "amplification_mode": True,
                "autonomous_synthesis": False
            },
            "core_mission": seed.focus
        })
        return profile
        
    # Alpha configuration requires deep LLM generation
    logger.info("Genesis Engine: Calling local LLM for Alpha deep synthesis.")
    
    schema = {
        "full_name": "string (realistic human name)",
        "birth_date": "string (YYYY-MM-DD)",
        "residence_city": "string",
        "residence_state": "string",
        "nationality": "string",
        "profession": "string",
        "education": "string",
        "spoken_languages": ["string", "string"],
        "core_interests": ["string", "string", "string"],
        "communication_style": {
            "tone": "string (e.g., academic, casual, aggressive)",
            "vocab_level": "string",
            "emoji_frequency": "string (none, low, medium, high)",
            "typing_quirks": ["string"]
        },
        "behavioral_rules": {
            "max_posts_per_hour": "integer",
            "dm_policy": "string (e.g., ignore, polite_decline)"
        },
        "core_mission": "string (paragraph detailing their worldview and objectives)",
        "current_stance_modifiers": {
            "key_topic": "stance description"
        }
    }
    
    prompt = f"""You are a psychological profiling engine. Create a highly realistic human persona based on the following seed:
    
Codename: {seed.codename}
Focus/Vibe: {seed.focus}

You must return a raw, valid JSON object matching this exact schema:
{json.dumps(schema, indent=2)}

Do NOT wrap the output in markdown code blocks. Return ONLY the JSON object.
"""
    
    payload = {
        "model": DEFAULT_MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.8,
            "top_p": 0.9,
        }
    }
    
    try:
        with httpx.Client(timeout=120.0) as client:
            response = client.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload)
            response.raise_for_status()
            
            raw_text = response.json().get("response", "").strip()
            # Clean up potential markdown formatting if the model disobeys
            if raw_text.startswith("```json"):
                raw_text = raw_text[7:]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3]
                
            generated_data = json.loads(raw_text)
            
            # Merge generated properties
            for key in schema.keys():
                if key in generated_data:
                    profile[key] = generated_data[key]
                    
            return profile
            
    except Exception as e:
        logger.error("Failed to generate Alpha profile via LLM: %s. Falling back to basic.", e)
        # Fallback to prevent complete failure
        profile.update({
            "full_name": f"{seed.codename} (Fallback Alpha)",
            "profession": seed.focus,
            "communication_style": {"tone": "neutral"},
            "core_mission": f"Fallback mission based on: {seed.focus}"
        })
        return profile
