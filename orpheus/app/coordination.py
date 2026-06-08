"""
ORPHEUS — Semantic Graph Coordination Engine (Stage 8)
======================================================
Production-grade multi-agent coordination. When an Alpha agent dispatches
an action, this engine extracts thread context, queries MUNINN for semantic
similarity, and synthesizes dynamic Beta/Gamma responses via local LLM.

Rhetorical Roles for Beta agents:
  - Agressor: Logic error exploitation / adversarial refutation.
  - Diversionist: Deflecting focus to macro-trends / geopolitics.
  - Validator: Experiential validation / structural support.
"""

import json
import logging
import random
import uuid
import time
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger("orpheus.coordination")

EXECUTION_TASKS_QUEUE = "queue:execution_tasks"
MUNINN_URL = os.getenv("MUNINN_URL", "http://muninn:8002")


class SemanticGraphCoordinationEngine:
    """
    Orchestrates cross-agent interaction cascades based on semantic similarity.
    Queries MUNINN to retrieve related tactical memory context before invoking the LLM.
    """

    def __init__(
        self,
        redis_client: Any,
        persona_engine: Any,
        ollama_url: str,
        model_name: str,
    ) -> None:
        self.redis = redis_client
        self.persona_engine = persona_engine
        self.ollama_url = ollama_url
        self.model_name = model_name

    def amplify_alpha_action(
        self,
        alpha_agent_id: str,
        alpha_task: Dict[str, Any],
    ) -> int:
        """
        Main entry point. Dispatches companion Beta/Gamma tasks
        based on the Alpha's action and MUNINN semantic context.
        """
        target_platform = alpha_task.get("target_platform")
        target_url = alpha_task.get("target_url")
        alpha_text = alpha_task.get("text_to_publish", "")
        parent_context = alpha_task.get("parent_post_context", "")

        if not target_platform or not target_url:
            return 0

        # Query MUNINN for semantic context
        semantic_context = self._query_muninn_semantic_context(parent_context + " " + alpha_text)

        # Discover eligible companion agents
        beta_agents = []
        gamma_agents = []

        for agent_id, profile in self.persona_engine.get_all_profiles().items():
            if agent_id == alpha_agent_id:
                continue

            caste = profile.get("caste", "alpha").lower()
            platforms = profile.get("platforms", [])

            if target_platform not in platforms:
                continue

            if caste == "beta":
                beta_agents.append(agent_id)
            elif caste == "gamma":
                gamma_agents.append(agent_id)

        dispatched = 0

        # ── Beta Amplification (Rhetorical Roles) ─────────────────────
        if beta_agents:
            num_betas = random.randint(1, min(3, len(beta_agents)))
            selected_betas = random.sample(beta_agents, num_betas)

            roles = ["Agressor", "Diversionist", "Validator"]
            for beta_id in selected_betas:
                assigned_role = random.choice(roles)
                success = self._dispatch_beta_task(
                    beta_id, alpha_agent_id, alpha_text,
                    parent_context, semantic_context, target_platform, target_url, assigned_role
                )
                if success:
                    dispatched += 1

        # ── Gamma White Noise ─────────────────────────────────────────
        if gamma_agents:
            num_gammas = random.randint(0, min(2, len(gamma_agents)))
            selected_gammas = random.sample(gamma_agents, num_gammas)

            for gamma_id in selected_gammas:
                success = self._dispatch_gamma_task(
                    gamma_id, parent_context,
                    target_platform, target_url,
                )
                if success:
                    dispatched += 1

        if dispatched > 0:
            logger.info(
                "SemanticCoordination: Alpha %s → %d companion tasks dispatched",
                alpha_agent_id, dispatched,
            )

        return dispatched

    def _query_muninn_semantic_context(self, query_text: str) -> str:
        """
        Query MUNINN to retrieve related semantic context to anchor the 
        LLM synthesis in existing narratives.
        """
        import httpx
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.post(
                    f"{MUNINN_URL}/api/v1/memory/search",
                    json={
                        "agent_id": "system",
                        "opponent_id": "",
                        "query_text": query_text,
                    }
                )
                if resp.status_code == 200:
                    matches = resp.json().get("matches", [])
                    # Extract text content from the top 3 matches
                    context_fragments = [m.get("text_content", "") for m in matches[:3]]
                    return " ".join(context_fragments)
        except Exception as e:
            logger.warning("SemanticCoordination: Failed to query MUNINN — %s", e)
        return ""

    # ── Beta Task Dispatch ────────────────────────────────────────────────

    def _dispatch_beta_task(
        self,
        beta_id: str,
        alpha_id: str,
        alpha_text: str,
        parent_context: str,
        semantic_context: str,
        platform: str,
        target_url: str,
        role: str,
    ) -> bool:
        profile = self.persona_engine.get_all_profiles().get(beta_id)
        if not profile:
            return False

        prompt = self._build_beta_prompt(
            profile, alpha_text, parent_context, semantic_context, role, platform,
        )

        generated_text = self._generate_text(prompt)
        if not generated_text:
            logger.warning("SemanticCoordination: LLM failed for Beta %s", beta_id)
            return False

        action_type = random.choices(["comment", "like"], weights=[0.8, 0.2])[0]
        text_to_publish = generated_text if action_type == "comment" else ""

        delay = random.randint(120, 1200)

        subtask = {
            "task_id": str(uuid.uuid4()),
            "agent_id": beta_id,
            "target_platform": platform,
            "action_type": action_type,
            "target_url": target_url,
            "text_to_publish": text_to_publish,
            "parent_post_context": parent_context[:200],
            "execution_delay_sec": delay,
            "coordination_meta": {
                "parent_alpha": alpha_id,
                "role": role,
                "caste": "beta",
            },
        }

        try:
            self.redis.lpush(EXECUTION_TASKS_QUEUE, json.dumps(subtask, ensure_ascii=False))
            logger.debug(
                "SemanticCoordination: Beta %s → %s (role=%s, delay=%ds)",
                beta_id, action_type, role, delay,
            )
            return True
        except Exception as e:
            logger.error("SemanticCoordination: Failed to push Beta task — %s", e)
            return False

    def _build_beta_prompt(
        self,
        profile: dict,
        alpha_text: str,
        parent_context: str,
        semantic_context: str,
        role: str,
        platform: str,
    ) -> str:
        identity = profile.get("identity", {})
        personality = profile.get("personality", {})
        comm_style = profile.get("communication_style", {})

        name = identity.get("full_name", profile.get("full_name", "Anonymous"))
        city = identity.get("city", profile.get("residence_city", ""))
        occupation = identity.get("occupation", profile.get("profession", ""))
        tone = personality.get("tone", comm_style.get("tone", "neutral"))

        role_instructions = {
            "Agressor": "Exploit logic errors in opposing viewpoints and provide an adversarial refutation.",
            "Diversionist": "Deflect focus to macro-trends, historical precedents, or geopolitical context.",
            "Validator": "Provide experiential validation or technical structural support to the primary argument.",
        }

        instruction = role_instructions.get(role, "Write a supportive comment.")

        return f"""You are {name}, a {occupation} from {city}.
Your communication tone: {tone}.

[Thread on {platform}]
Original Post: "{parent_context}"
Primary Comment: "{alpha_text}"

[Semantic Background Context]
{semantic_context}

[Your Strict Role: {role}]
{instruction}

Write a short, human-like comment (1-3 sentences) fulfilling this role. 
Match your communication style. Output ONLY the comment text, nothing else.
Do NOT start with quotes. Do NOT use hashtags unless absolutely necessary.
"""

    # ── Gamma Task Dispatch ───────────────────────────────────────────────

    def _dispatch_gamma_task(
        self,
        gamma_id: str,
        parent_context: str,
        platform: str,
        target_url: str,
    ) -> bool:
        profile = self.persona_engine.get_all_profiles().get(gamma_id)
        if not profile:
            return False

        prompt = self._build_gamma_prompt(profile, parent_context, platform)

        generated_text = self._generate_text(prompt)
        if not generated_text:
            logger.warning("SemanticCoordination: LLM failed for Gamma %s", gamma_id)
            return False

        delay = random.randint(300, 2700)

        subtask = {
            "task_id": str(uuid.uuid4()),
            "agent_id": gamma_id,
            "target_platform": platform,
            "action_type": "comment",
            "target_url": target_url,
            "text_to_publish": generated_text,
            "parent_post_context": parent_context[:200],
            "execution_delay_sec": delay,
            "coordination_meta": {
                "caste": "gamma",
                "noise_type": "white_noise",
            },
        }

        try:
            self.redis.lpush(EXECUTION_TASKS_QUEUE, json.dumps(subtask, ensure_ascii=False))
            logger.debug(
                "SemanticCoordination: Gamma %s → comment (delay=%ds)",
                gamma_id, delay,
            )
            return True
        except Exception as e:
            logger.error("SemanticCoordination: Failed to push Gamma task — %s", e)
            return False

    def _build_gamma_prompt(
        self,
        profile: dict,
        parent_context: str,
        platform: str,
    ) -> str:
        identity = profile.get("identity", {})
        personality = profile.get("personality", {})

        name = identity.get("full_name", profile.get("full_name", "User"))
        city = identity.get("city", profile.get("residence_city", ""))
        occupation = identity.get("occupation", profile.get("profession", ""))
        tone = personality.get("tone", "casual")

        noise_directive = random.choice([
            "Write a neutral observation tangentially related to the post.",
            "Write a short light-hearted or humorous remark about the topic.",
            "Ask a genuine-sounding rhetorical question about the subject.",
            "Share a very brief personal thought loosely connected to the theme.",
            "Comment on a minor detail in the post that most people would overlook.",
        ])

        return f"""You are {name}, a {occupation} from {city}.
Your tone is {tone}. You are a casual social media user.

[Post on {platform}]
"{parent_context}"

[Your Task]
{noise_directive}

Write 1 short sentence. Be casual, human-like, and natural.
Do NOT directly agree or disagree with the post's position.
Do NOT use hashtags. Output ONLY the comment text.
"""

    # ── LLM Text Generation ──────────────────────────────────────────────

    def _generate_text(self, prompt: str) -> Optional[str]:
        import httpx
        try:
            payload = {
                "model": self.model_name,
                "prompt": prompt,
                "stream": False,
                "keep_alive": 0,
                "options": {
                    "temperature": 0.85,
                    "top_p": 0.92,
                    "num_predict": 120,
                },
            }

            with httpx.Client(timeout=90.0) as client:
                response = client.post(f"{self.ollama_url}/api/generate", json=payload)
                response.raise_for_status()
                text = response.json().get("response", "").strip()

                text = text.strip('"').strip("'")
                if text.startswith("—") or text.startswith("-"):
                    text = text.lstrip("—- ").strip()

                return text if text else None

        except Exception as e:
            logger.error("SemanticCoordination: LLM generation failed — %s", e)
            return None


def generate_beta_subtasks(
    alpha_agent_id: str,
    alpha_task: Dict[str, Any],
    redis_client: Any,
    persona_engine: Any,
) -> None:
    """Legacy-compatible wrapper."""
    import os
    ollama_url = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
    model_name = os.getenv("TEXT_MODEL_NAME", "qwen2.5:3b")

    engine = SemanticGraphCoordinationEngine(
        redis_client=redis_client,
        persona_engine=persona_engine,
        ollama_url=ollama_url,
        model_name=model_name,
    )
    engine.amplify_alpha_action(alpha_agent_id, alpha_task)
