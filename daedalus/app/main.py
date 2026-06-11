"""
DAEDALUS — FastAPI Application Entrypoint
==========================================
Admin portal for PROJECT MORPHEUS.

Provides:
  - JWT authentication (/api/v1/auth/login)
  - Agent management (/api/v1/agents)
  - Campaign management (/api/v1/campaigns)
  - Database Explorer (/api/v1/db)
  - Health check (/)
"""

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Optional

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db, init_tables
from app.db_explorer import router as db_explorer_router
from app.analytics import router as analytics_router
from app.landscape import router as landscape_router
from app.souls import router as souls_router
from app.router_huginn_control import router as huginn_control_router
from app.router_auth_factory import router as auth_factory_router
from app.router_sandbox import router as sandbox_router
from app.router_missions import router as missions_router
from app.router_scouting import router as scouting_router
from app.models import AdminUser, Role, RolePermission, SoulAccount
from app.rbac import (
    ATOM_PERMISSIONS,
    TokenResponse,
    authenticate_user,
    create_access_token,
    get_current_user,
    hash_password,
    require_permission,
)

# ── Logging ───────────────────────────────────────────────────────────────

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("daedalus")


# ── Lifespan (startup / shutdown) ─────────────────────────────────────────

@asynccontextmanager
async def lifespan(application: FastAPI):
    """Initialize database tables and seed SuperAdmin on startup."""
    logger.info("DAEDALUS starting — initializing database tables...")
    init_tables()
    _seed_superadmin()

    # Stage 17: launch the Mission Control DAG reconciler background thread.
    from app.mission_control import start_reconciler, stop_reconciler
    start_reconciler()

    # Stage 19: launch the data-retention pruning scheduler (non-blocking asyncio task).
    import asyncio
    from app.retention_policy import retention_loop, stop_retention
    retention_task = asyncio.create_task(retention_loop())

    logger.info("DAEDALUS ready.")
    yield
    stop_retention()
    retention_task.cancel()
    stop_reconciler()
    logger.info("DAEDALUS shutting down.")


def _seed_superadmin() -> None:
    """
    Pre-populate the database with a SuperAdmin account and a base
    'SuperAdmin' role that holds every atom permission.
    Idempotent — skips if the SuperAdmin user already exists.
    """
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        sa_username = os.getenv("SUPERADMIN_USERNAME", "morpheus")
        sa_password = os.getenv("SUPERADMIN_PASSWORD", "CHANGE_ME_IMMEDIATELY")

        existing = db.query(AdminUser).filter(AdminUser.username == sa_username).first()
        if existing is not None:
            logger.info("SuperAdmin '%s' already exists — skipping seed.", sa_username)
            return

        # Create the SuperAdmin role with every atom permission
        sa_role = Role(name="SuperAdmin", description="Absolute access — all permissions granted")
        db.add(sa_role)
        db.flush()

        for perm_name in ATOM_PERMISSIONS:
            db.add(RolePermission(role_id=sa_role.id, permission=perm_name))

        # Create the SuperAdmin user
        sa_user = AdminUser(
            username=sa_username,
            password_hash=hash_password(sa_password),
            is_superadmin=True,
            is_active=True,
        )
        sa_user.roles.append(sa_role)
        db.add(sa_user)

        db.commit()
        logger.info(
            "Seeded SuperAdmin user '%s' with role 'SuperAdmin' and %d atom permissions.",
            sa_username,
            len(ATOM_PERMISSIONS),
        )
    except Exception:
        db.rollback()
        logger.exception("Failed to seed SuperAdmin.")
    finally:
        db.close()


# ── FastAPI Application ───────────────────────────────────────────────────

app = FastAPI(
    title="DAEDALUS — MORPHEUS Admin Portal",
    description="Centralized control panel for the MORPHEUS multi-agent system",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Include sub-routers ──────────────────────────────────────────────────

app.include_router(db_explorer_router)
app.include_router(analytics_router)
app.include_router(landscape_router)
app.include_router(souls_router)
app.include_router(huginn_control_router)
app.include_router(auth_factory_router)
app.include_router(sandbox_router)
app.include_router(missions_router)
app.include_router(scouting_router)

# ── Static Frontend SPA ──────────────────────────────────────────────────

STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    # Vite build outputs JS/CSS to /assets/ — mount explicitly so the SPA can load them
    assets_dir = STATIC_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")


# ── Pydantic request/response schemas ────────────────────────────────────

class HealthResponse(BaseModel):
    service: str
    status: str
    version: str


class AgentCreateRequest(BaseModel):
    agent_id: str
    platform: str
    username: str
    password: str
    auth_cookies: Optional[dict] = None
    assigned_proxy: Optional[str] = None


class AgentResponse(BaseModel):
    id: int
    agent_id: str
    platform: str
    username: str
    assigned_proxy: Optional[str]
    status: str

    class Config:
        from_attributes = True


class AgentListResponse(BaseModel):
    agents: list[AgentResponse]
    total: int


class CampaignCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    target_platforms: list[str]
    agent_ids: list[str]
    layers_filter: Optional[dict] = None


class CampaignResponse(BaseModel):
    campaign_id: str
    name: str
    status: str
    message: str


class RoleCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    permissions: list[str]


class RoleResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    permissions: list[str]

    class Config:
        from_attributes = True


class UserInfoResponse(BaseModel):
    id: int
    username: str
    is_superadmin: bool
    is_active: bool
    roles: list[str]
    permissions: list[str]


# ── Health Check ──────────────────────────────────────────────────────────

@app.get("/api/v1/health", response_model=HealthResponse, tags=["System"])
def health_check() -> HealthResponse:
    """Service health and version check."""
    return HealthResponse(service="daedalus", status="operational", version="0.1.0")


# ── Authentication ────────────────────────────────────────────────────────

@app.post("/api/v1/auth/login", response_model=TokenResponse, tags=["Auth"])
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> TokenResponse:
    """
    Authenticate with username/password and receive a JWT bearer token.
    """
    user = authenticate_user(db, form_data.username, form_data.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token(user)
    logger.info("User '%s' authenticated successfully.", user.username)
    return TokenResponse(access_token=token)


@app.get("/api/v1/auth/me", response_model=UserInfoResponse, tags=["Auth"])
def get_me(
    current_user: AdminUser = Depends(get_current_user),
) -> UserInfoResponse:
    """Return profile information for the currently authenticated user."""
    all_permissions: set[str] = set()
    role_names: list[str] = []
    for role in current_user.roles:
        role_names.append(role.name)
        for rp in role.permissions:
            all_permissions.add(rp.permission)

    return UserInfoResponse(
        id=current_user.id,
        username=current_user.username,
        is_superadmin=current_user.is_superadmin,
        is_active=current_user.is_active,
        roles=role_names,
        permissions=sorted(all_permissions),
    )


# ── Agent Management (/api/v1/agents) ────────────────────────────────────

@app.get("/api/v1/agents", response_model=AgentListResponse, tags=["Agents"])
def list_agents(
    platform: Optional[str] = None,
    agent_id: Optional[str] = None,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("agents:view")),
) -> AgentListResponse:
    """List all registered agent accounts, with optional filters."""
    query = db.query(SoulAccount)
    if platform:
        query = query.filter(SoulAccount.platform == platform)
    if agent_id:
        query = query.filter(SoulAccount.agent_id == agent_id)

    accounts = query.all()
    return AgentListResponse(
        agents=[
            AgentResponse(
                id=a.id,
                agent_id=a.agent_id,
                platform=a.platform,
                username=a.username,
                assigned_proxy=a.assigned_proxy,
                status=a.status,
            )
            for a in accounts
        ],
        total=len(accounts),
    )


@app.post("/api/v1/agents", response_model=AgentResponse, status_code=201, tags=["Agents"])
def create_agent(
    request: AgentCreateRequest,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("agents:manage")),
) -> AgentResponse:
    """
    Register a new agent social-media account.
    The SuperAdmin manually registers the account on a physical device,
    then enters the credentials here for MYRMIDON to consume.
    """
    account = SoulAccount(
        agent_id=request.agent_id,
        platform=request.platform,
        username=request.username,
        password_hash=hash_password(request.password),
        auth_cookies=request.auth_cookies,
        assigned_proxy=request.assigned_proxy,
        status="active",
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    logger.info(
        "Agent account created: agent_id=%s, platform=%s, username=%s",
        account.agent_id,
        account.platform,
        account.username,
    )
    return AgentResponse(
        id=account.id,
        agent_id=account.agent_id,
        platform=account.platform,
        username=account.username,
        assigned_proxy=account.assigned_proxy,
        status=account.status,
    )


@app.delete("/api/v1/agents/{account_id}", tags=["Agents"])
def deactivate_agent(
    account_id: int,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("agents:manage")),
) -> dict[str, str]:
    """Soft-delete an agent account by setting its status to 'disabled'."""
    account = db.query(SoulAccount).filter(SoulAccount.id == account_id).first()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    account.status = "disabled"
    db.commit()
    logger.info("Agent account %d deactivated.", account_id)
    return {"status": "success", "message": f"Account {account_id} deactivated"}


# ── Campaign Management (/api/v1/campaigns) ──────────────────────────────

@app.post("/api/v1/campaigns", response_model=CampaignResponse, tags=["Campaigns"])
def create_campaign(
    request: CampaignCreateRequest,
    _user: AdminUser = Depends(require_permission("campaigns:create")),
) -> CampaignResponse:
    """
    Create a new information campaign.
    Stage 1: stores the campaign definition and returns a tracking ID.
    Full campaign orchestration (HUGINN + ORPHEUS + MYRMIDON pipeline)
    will be implemented in later stages.
    """
    import uuid

    campaign_id = str(uuid.uuid4())
    logger.info(
        "Campaign created: id=%s, name='%s', platforms=%s, agents=%s",
        campaign_id,
        request.name,
        request.target_platforms,
        request.agent_ids,
    )
    return CampaignResponse(
        campaign_id=campaign_id,
        name=request.name,
        status="created",
        message="Campaign registered. Orchestration pipeline activation pending Stage 5 implementation.",
    )


@app.get("/api/v1/campaigns", tags=["Campaigns"])
def list_campaigns(
    _user: AdminUser = Depends(require_permission("campaigns:view")),
) -> dict[str, Any]:
    """
    List active campaigns.
    Full persistence layer for campaigns will be added in later stages.
    """
    return {
        "campaigns": [],
        "total": 0,
        "message": "Campaign persistence will be implemented in Stage 5.",
    }


# ── Role Management (/api/v1/roles) ──────────────────────────────────────

@app.post("/api/v1/roles", response_model=RoleResponse, status_code=201, tags=["Roles"])
def create_role(
    request: RoleCreateRequest,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("roles:manage")),
) -> RoleResponse:
    """
    Dynamically create a new RBAC role with a set of atom permissions.
    Only SuperAdmin or users with 'roles:manage' can create roles.
    """
    # Validate all permissions are known atoms
    invalid = set(request.permissions) - set(ATOM_PERMISSIONS)
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown permissions: {sorted(invalid)}. Valid: {sorted(ATOM_PERMISSIONS)}",
        )

    existing = db.query(Role).filter(Role.name == request.name).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Role '{request.name}' already exists")

    role = Role(name=request.name, description=request.description)
    db.add(role)
    db.flush()

    for perm_name in request.permissions:
        db.add(RolePermission(role_id=role.id, permission=perm_name))

    db.commit()
    db.refresh(role)

    logger.info("Role created: name='%s', permissions=%s", role.name, request.permissions)
    return RoleResponse(
        id=role.id,
        name=role.name,
        description=role.description,
        permissions=[rp.permission for rp in role.permissions],
    )


@app.get("/api/v1/roles", tags=["Roles"])
def list_roles(
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("roles:view")),
) -> dict[str, Any]:
    """List all defined RBAC roles with their permissions."""
    roles = db.query(Role).all()
    return {
        "roles": [
            {
                "id": r.id,
                "name": r.name,
                "description": r.description,
                "permissions": [rp.permission for rp in r.permissions],
            }
            for r in roles
        ],
        "total": len(roles),
    }


@app.get("/api/v1/permissions", tags=["Roles"])
def list_available_permissions(
    _user: AdminUser = Depends(get_current_user),
) -> dict[str, list[str]]:
    """Return the full list of available atom permissions in the system."""
    return {"permissions": sorted(ATOM_PERMISSIONS)}


# ── SPA Fallback (must be last) ──────────────────────────────────────────

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
@app.get("/{full_path:path}", response_class=HTMLResponse, include_in_schema=False)
async def spa_fallback(request: Request, full_path: str = ""):
    """Serve the SPA index.html for all non-API routes."""
    if full_path.startswith("api/") or full_path.startswith("static/"):
        raise HTTPException(status_code=404)
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return HTMLResponse(content=index_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>DAEDALUS</h1><p>Frontend not built.</p>")
