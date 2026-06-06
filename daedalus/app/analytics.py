"""
DAEDALUS — Analytics Endpoints
================================
Provides execution metrics, queue depths, memory auditing,
and activity stream for the MORPHEUS dashboard.
"""

import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
import httpx
import redis
from sqlalchemy.orm import Session
from sqlalchemy import func, desc

from app.database import get_db
from app.models import AdminUser, SoulAccount, AgentActivityLog
from app.rbac import require_permission

router = APIRouter(prefix="/api/v1/analytics", tags=["Analytics"])

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
MUNINN_URL = os.getenv("MUNINN_URL", "http://muninn:8002")


def get_redis() -> redis.Redis:
    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=0,
        decode_responses=True
    )


@router.get("/metrics")
def get_metrics(
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("agents:view")),
) -> Dict[str, Any]:
    """
    Returns global system metrics including comments sent,
    guardrail verification rates, and agent status distribution.
    """
    redis_client = get_redis()

    # Read counters from Redis
    comments_sent = int(redis_client.get("metrics:comments_sent") or 0)
    guardrail_failures = int(redis_client.get("metrics:guardrail_failures") or 0)
    guardrail_successes = int(redis_client.get("metrics:guardrail_successes") or 0)

    total_guardrail_checks = guardrail_failures + guardrail_successes
    failure_ratio = (guardrail_failures / total_guardrail_checks) if total_guardrail_checks > 0 else 0.0

    # Aggregate account statuses from PostgreSQL
    status_counts = db.query(
        SoulAccount.status, func.count(SoulAccount.id)
    ).group_by(SoulAccount.status).all()

    status_dict = {status: count for status, count in status_counts}
    active_accounts = status_dict.get("active", 0)
    banned_accounts = status_dict.get("disabled", 0) + status_dict.get("banned", 0)

    return {
        "metrics": {
            "total_comments_sent": comments_sent,
            "guardrail_failure_ratio": round(failure_ratio, 4),
            "guardrail_checks_total": total_guardrail_checks
        },
        "agents": {
            "active": active_accounts,
            "banned_or_disabled": banned_accounts,
            "total": active_accounts + banned_accounts
        }
    }


@router.get("/queues")
def get_queues(
    _user: AdminUser = Depends(require_permission("agents:view")),
) -> Dict[str, Any]:
    """Interactive queue depth inspector."""
    redis_client = get_redis()

    raw_depth = redis_client.llen("queue:raw_events")
    exec_depth = redis_client.llen("queue:execution_tasks")
    activity_depth = redis_client.llen("queue:activity_logs")

    return {
        "queues": {
            "queue:raw_events": raw_depth,
            "queue:execution_tasks": exec_depth,
            "queue:activity_logs": activity_depth,
        }
    }


@router.get("/memory-audit/{agent_id}")
def memory_audit(
    agent_id: str,
    _user: AdminUser = Depends(require_permission("agents:view")),
) -> Dict[str, Any]:
    """
    Proxies to MUNINN to retrieve all indexed opponent interactions
    and dialog summaries for a given agent.
    """
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(f"{MUNINN_URL}/api/v1/memory/audit/{agent_id}")
            if resp.status_code == 200:
                return resp.json()

            # If MUNINN doesn't have a dedicated audit endpoint,
            # fall back to a search-based approach
            resp = client.post(
                f"{MUNINN_URL}/api/v1/memory/search",
                json={
                    "agent_id": agent_id,
                    "opponent_id": "",
                    "query_text": "",
                }
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "agent_id": agent_id,
                    "memory_entries": data.get("matches", []),
                    "total": len(data.get("matches", [])),
                    "source": "search_fallback"
                }
            else:
                return {
                    "agent_id": agent_id,
                    "memory_entries": [],
                    "total": 0,
                    "error": f"MUNINN returned status {resp.status_code}"
                }
    except Exception as e:
        return {
            "agent_id": agent_id,
            "memory_entries": [],
            "total": 0,
            "error": f"Failed to reach MUNINN: {str(e)}"
        }


@router.get("/stream")
def activity_stream(
    agent_id: Optional[str] = None,
    platform: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("agents:view")),
) -> Dict[str, Any]:
    """
    Paginated query of the agent_activity_logs table.
    Returns the most recent activity first.
    """
    query = db.query(AgentActivityLog)

    if agent_id:
        query = query.filter(AgentActivityLog.agent_id == agent_id)
    if platform:
        query = query.filter(AgentActivityLog.platform == platform)

    total = query.count()
    logs = query.order_by(desc(AgentActivityLog.created_at)).offset(offset).limit(limit).all()

    return {
        "logs": [
            {
                "id": log.id,
                "agent_id": log.agent_id,
                "platform": log.platform,
                "action_type": log.action_type,
                "target_url": log.target_url,
                "text_content": log.text_content,
                "status": log.status,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }
