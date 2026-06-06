"""
ORPHEUS — Alpha-to-Beta Coordination Engine
=============================================
Orchestrates autonomous cross-agent interaction. When an Alpha agent
executes a primary task, this engine amplifies it by spawning
dependent sub-tasks for Beta-caste agents (likes, reposts, generic agreements).
"""

import json
import logging
import random
import uuid
import time
from typing import Dict, Any

logger = logging.getLogger("orpheus.coordination")

EXECUTION_TASKS_QUEUE = "queue:execution_tasks"

def generate_beta_subtasks(
    alpha_agent_id: str,
    alpha_task: Dict[str, Any],
    redis_client,
    persona_engine
):
    """
    Finds Beta agents compatible with the target platform and
    pushes delayed interaction tasks to amplify the Alpha's action.
    """
    target_platform = alpha_task.get("target_platform")
    target_url = alpha_task.get("target_url")
    
    if not target_platform or not target_url:
        return

    beta_agents = []
    
    # Identify compatible Beta agents from loaded profiles
    for agent_id, profile in persona_engine.profiles.items():
        if agent_id == alpha_agent_id:
            continue
            
        caste = profile.get("caste", "alpha").lower()
        platforms = profile.get("platforms", [])
        
        if caste == "beta" and target_platform in platforms:
            beta_agents.append(agent_id)
            
    if not beta_agents:
        logger.debug("No eligible Beta agents found for platform %s", target_platform)
        return
        
    # Pick 1-3 random betas to amplify
    num_betas = random.randint(1, min(3, len(beta_agents)))
    selected_betas = random.sample(beta_agents, num_betas)
    
    logger.info("Amplifying Alpha (%s) task with Betas: %s", alpha_agent_id, selected_betas)
    
    for beta_id in selected_betas:
        # Action distribution for betas
        # mostly likes, some reposts/short comments
        action_type = random.choices(
            ["like", "repost", "comment"],
            weights=[0.6, 0.2, 0.2]
        )[0]
        
        text_to_publish = ""
        if action_type == "comment":
            # Very simple generic agreement
            text_to_publish = random.choice([
                "Totally agree with this.",
                "Well said!",
                "100% this.",
                "Exactly what I was thinking.",
                "This is the real issue right here."
            ])
            
        # Add a realistic organic delay: 1 minute to 15 minutes after Alpha
        delay = random.randint(60, 900)
        
        subtask = {
            "task_id": str(uuid.uuid4()),
            "agent_id": beta_id,
            "target_platform": target_platform,
            "action_type": action_type,
            "target_url": target_url,
            "text_to_publish": text_to_publish,
            "parent_post_context": alpha_task.get("text_to_publish", "")[:100],
            "execution_delay_sec": delay
        }
        
        try:
            redis_client.lpush(EXECUTION_TASKS_QUEUE, json.dumps(subtask, ensure_ascii=False))
            logger.debug("Pushed Beta subtask %s for agent %s (delay %ds)", action_type, beta_id, delay)
        except Exception as e:
            logger.error("Failed to push Beta subtask: %s", e)
