"""
DAEDALUS — Analytics Endpoints
================================
Provides execution metrics and queue depths for the MORPHEUS dashboard.
Reads from Redis and PostgreSQL.
"""

import os
from typing import Dict, Any

from fastapi import APIRouter, Depends
import redis
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models import AdminUser, SoulAccount
from app.rbac import require_permission

router = APIRouter(prefix="/api/v1/analytics", tags=["Analytics"])

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

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
    """
    Interactive queue depth inspector.
    """
    redis_client = get_redis()
    
    raw_depth = redis_client.llen("queue:raw_events")
    exec_depth = redis_client.llen("queue:execution_tasks")
    
    return {
        "queues": {
            "queue:raw_events": raw_depth,
            "queue:execution_tasks": exec_depth
        }
    }
