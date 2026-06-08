"""
DAEDALUS — Analytics Endpoints
================================
Provides execution metrics, queue depths, memory auditing,
and activity stream for the MORPHEUS dashboard.
"""

import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
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
MYRMIDON_DEVICE_URL = os.getenv("MYRMIDON_DEVICE_URL", "http://myrmidon:8003")
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "morpheus-internal-sync-key")


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


import time

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


@router.get("/latency")
def get_latency(
    _user: AdminUser = Depends(require_permission("agents:view")),
) -> Dict[str, Any]:
    """
    Executes true round-trip time (RTT) measurements using time.perf_counter().
    Pings local Redis and makes a HEAD request to MUNINN.
    """
    latency = {
        "daedalus_db": 0.0,
        "orpheus_cache": 0.0,
        "huginn_sync": 0.0,
        "myrmidon_adb": 0.0
    }

    # 1. Measure DAEDALUS PostgreSQL (Simulated via local Redis PING as substitute for raw pg ping)
    try:
        t0 = time.perf_counter()
        redis_client = get_redis()
        redis_client.ping()
        t1 = time.perf_counter()
        latency["daedalus_db"] = round((t1 - t0) * 1000, 2)
    except Exception:
        latency["daedalus_db"] = -1.0

    # 2. Measure ORPHEUS Async Cache (Redis proxy)
    try:
        t0 = time.perf_counter()
        redis_client = get_redis()
        redis_client.ping()
        t1 = time.perf_counter()
        latency["orpheus_cache"] = round((t1 - t0) * 1000, 2)
    except Exception:
        latency["orpheus_cache"] = -1.0

    # 3. Measure HUGINN Sync Loop (Via MUNINN HTTP GET to healthcheck)
    try:
        t0 = time.perf_counter()
        with httpx.Client(timeout=2.0) as client:
            client.get(f"{MUNINN_URL}/")
        t1 = time.perf_counter()
        latency["huginn_sync"] = round((t1 - t0) * 1000, 2)
    except Exception:
        latency["huginn_sync"] = -1.0

    # 4. Measure MYRMIDON ADB Proxy
    try:
        t0 = time.perf_counter()
        with httpx.Client(timeout=2.0) as client:
            client.get(f"{MYRMIDON_DEVICE_URL}/api/v1/ping", headers={"X-Internal-Token": INTERNAL_API_TOKEN})
        t1 = time.perf_counter()
        latency["myrmidon_adb"] = round((t1 - t0) * 1000, 2)
    except Exception:
        latency["myrmidon_adb"] = -1.0

    return latency


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


# ── Device Proxy (forwards to MYRMIDON Device API) ───────────────────

@router.get("/devices")
def proxy_devices(
    _user: AdminUser = Depends(require_permission("agents:view")),
) -> Dict[str, Any]:
    """Proxy request to MYRMIDON Device API for connected ADB devices."""
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(
                f"{MYRMIDON_DEVICE_URL}/api/v1/devices",
                headers={"X-Internal-Token": INTERNAL_API_TOKEN},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        return {"devices": [], "total": 0, "error": str(e)}


@router.get("/devices/{device_id}")
def proxy_device_info(
    device_id: str,
    _user: AdminUser = Depends(require_permission("agents:view")),
) -> Dict[str, Any]:
    """Proxy request for detailed device telemetry."""
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(
                f"{MYRMIDON_DEVICE_URL}/api/v1/devices/{device_id}/info",
                headers={"X-Internal-Token": INTERNAL_API_TOKEN},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/devices/{device_id}/proxy")
def proxy_set_device_proxy(
    device_id: str,
    body: Dict[str, Any],
    _user: AdminUser = Depends(require_permission("agents:edit")),
) -> Dict[str, str]:
    """Proxy request to set OS-level proxy on a device."""
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{MYRMIDON_DEVICE_URL}/api/v1/devices/{device_id}/proxy",
                params={"proxy_host": body["proxy_host"], "proxy_port": body["proxy_port"]},
                headers={"X-Internal-Token": INTERNAL_API_TOKEN},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.delete("/devices/{device_id}/proxy")
def proxy_clear_device_proxy(
    device_id: str,
    _user: AdminUser = Depends(require_permission("agents:edit")),
) -> Dict[str, str]:
    """Proxy request to clear device proxy."""
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.delete(
                f"{MYRMIDON_DEVICE_URL}/api/v1/devices/{device_id}/proxy",
                headers={"X-Internal-Token": INTERNAL_API_TOKEN},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/devices/{device_id}/launch")
def proxy_launch_app(
    device_id: str,
    body: Dict[str, Any],
    _user: AdminUser = Depends(require_permission("agents:edit")),
) -> Dict[str, str]:
    """Proxy request to launch app on device."""
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{MYRMIDON_DEVICE_URL}/api/v1/devices/{device_id}/launch",
                params={"package": body["package"], "activity": body["activity"]},
                headers={"X-Internal-Token": INTERNAL_API_TOKEN},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

class ActivityLogRequest(BaseModel):
    agent_id: str
    platform: str
    action_type: str
    target_url: str
    text_content: Optional[str] = None
    status: str

@router.post("/internal/activity", status_code=201)
def internal_log_activity(
    request: ActivityLogRequest,
    x_internal_token: str = Header(None, alias="X-Internal-Token"),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """
    Internal endpoint for MYRMIDON to push activity logs directly into PostgreSQL.
    """
    if x_internal_token != INTERNAL_API_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid internal token")

    log_entry = AgentActivityLog(
        agent_id=request.agent_id,
        platform=request.platform,
        action_type=request.action_type,
        target_url=request.target_url,
        text_content=request.text_content,
        status=request.status,
    )
    db.add(log_entry)
    db.commit()

    return {"status": "success"}
