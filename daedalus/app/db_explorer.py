"""
DAEDALUS — Database Explorer Module
=====================================
Interactive API for directly querying, editing, and managing
PostgreSQL tables from the DAEDALUS admin panel.

Only SuperAdmin or users with 'db:read' / 'db:edit' permissions
can access these endpoints.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AdminUser
from app.rbac import require_permission

router = APIRouter(prefix="/api/v1/db", tags=["Database Explorer"])


# ── Pydantic request/response models ─────────────────────────────────────

class TableListResponse(BaseModel):
    tables: list[str]


class TableRow(BaseModel):
    data: dict[str, Any]


class TableDataResponse(BaseModel):
    table: str
    columns: list[str]
    rows: list[dict[str, Any]]
    total_count: int


class CellUpdateRequest(BaseModel):
    table: str
    primary_key_column: str
    primary_key_value: Any
    column: str
    new_value: Any


class CellUpdateResponse(BaseModel):
    status: str
    message: str


class RawQueryRequest(BaseModel):
    sql: str


class RawQueryResponse(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int


# ── Allowed tables (whitelist to prevent injection) ───────────────────────

ALLOWED_TABLES = {"admin_users", "roles", "role_permissions", "user_roles", "souls_accounts", "agent_profiles", "scraping_landscape", "agent_activity_logs"}


def _validate_table_name(table: str) -> None:
    """Raise 400 if the table name is not in the whitelist."""
    if table not in ALLOWED_TABLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Table '{table}' is not accessible. Allowed: {sorted(ALLOWED_TABLES)}",
        )


# ── Endpoints ─────────────────────────────────────────────────────────────

@router.get("/tables", response_model=TableListResponse)
def list_tables(
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("db:read")),
) -> TableListResponse:
    """Return the list of all user-defined tables in the public schema."""
    inspector = inspect(db.bind)
    tables = inspector.get_table_names(schema="public")
    return TableListResponse(tables=sorted(tables))


@router.get("/tables/{table_name}", response_model=TableDataResponse)
def read_table(
    table_name: str,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("db:read")),
) -> TableDataResponse:
    """
    Fetch rows from a specific table with pagination.
    Returns column names, row data as dicts, and total count.
    """
    _validate_table_name(table_name)

    count_result = db.execute(text(f"SELECT COUNT(*) FROM {table_name}"))  # noqa: S608
    total_count = count_result.scalar() or 0

    result = db.execute(
        text(f"SELECT * FROM {table_name} LIMIT :limit OFFSET :offset"),  # noqa: S608
        {"limit": limit, "offset": offset},
    )
    columns = list(result.keys())
    rows = [dict(zip(columns, row)) for row in result.fetchall()]

    return TableDataResponse(
        table=table_name,
        columns=columns,
        rows=rows,
        total_count=total_count,
    )


@router.put("/cell", response_model=CellUpdateResponse)
def update_cell(
    request: CellUpdateRequest,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("db:edit")),
) -> CellUpdateResponse:
    """
    Update a single cell in a table, identified by primary key.
    This is the core mechanism for the interactive DB Explorer UI.
    """
    _validate_table_name(request.table)

    stmt = text(
        f"UPDATE {request.table} "  # noqa: S608
        f"SET {request.column} = :new_value "
        f"WHERE {request.primary_key_column} = :pk_value"
    )
    result = db.execute(
        stmt,
        {"new_value": request.new_value, "pk_value": request.primary_key_value},
    )
    db.commit()

    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No matching row found for the given primary key.",
        )

    return CellUpdateResponse(
        status="success",
        message=f"Updated {request.table}.{request.column} where {request.primary_key_column}={request.primary_key_value}",
    )


@router.post("/query", response_model=RawQueryResponse)
def execute_raw_query(
    request: RawQueryRequest,
    db: Session = Depends(get_db),
    _user: AdminUser = Depends(require_permission("db:edit")),
) -> RawQueryResponse:
    """
    Execute a raw SQL query. Restricted to SuperAdmin or users with db:edit.
    Results are returned as a list of dicts.
    """
    try:
        result = db.execute(text(request.sql))
        if result.returns_rows:
            columns = list(result.keys())
            rows = [dict(zip(columns, row)) for row in result.fetchall()]
            row_count = len(rows)
        else:
            db.commit()
            columns = []
            rows = []
            row_count = result.rowcount
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"SQL execution error: {exc}",
        )

    return RawQueryResponse(columns=columns, rows=rows, row_count=row_count)
