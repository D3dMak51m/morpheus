"""
DAEDALUS — Database Initialization Script
===========================================
Standalone script to initialize the PostgreSQL schema and seed
the default SuperAdmin account with all base atom permissions.

Usage (from container or locally):
    python -m app.init_db

This script is idempotent: running it multiple times will not
create duplicate users or roles.
"""

import logging
import os
import sys

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("daedalus.init_db")


def main() -> None:
    """Initialize database schema and seed SuperAdmin."""
    from app.database import SessionLocal, init_tables
    from app.models import AdminUser, Role, RolePermission
    from app.rbac import ATOM_PERMISSIONS, hash_password

    logger.info("=" * 60)
    logger.info("DAEDALUS Database Initialization")
    logger.info("=" * 60)

    # Step 1: Create all tables
    logger.info("Step 1/3: Creating database tables...")
    init_tables()
    logger.info("All tables created successfully.")

    # Step 2: Seed SuperAdmin role with all atom permissions
    db = SessionLocal()
    try:
        sa_username = os.getenv("SUPERADMIN_USERNAME", "morpheus")
        sa_password = os.getenv("SUPERADMIN_PASSWORD", "CHANGE_ME_IMMEDIATELY")

        existing_user = db.query(AdminUser).filter(AdminUser.username == sa_username).first()
        if existing_user is not None:
            logger.info("SuperAdmin user '%s' already exists — skipping seed.", sa_username)
            logger.info("Database initialization complete (no changes needed).")
            return

        logger.info("Step 2/3: Creating SuperAdmin role with %d atom permissions...", len(ATOM_PERMISSIONS))

        sa_role = db.query(Role).filter(Role.name == "SuperAdmin").first()
        if sa_role is None:
            sa_role = Role(
                name="SuperAdmin",
                description="Absolute access — all permissions granted. Auto-created during init.",
            )
            db.add(sa_role)
            db.flush()

            for perm_name in ATOM_PERMISSIONS:
                db.add(RolePermission(role_id=sa_role.id, permission=perm_name))
            logger.info("SuperAdmin role created with permissions: %s", ATOM_PERMISSIONS)
        else:
            logger.info("SuperAdmin role already exists — reusing.")

        # Step 3: Create SuperAdmin user
        logger.info("Step 3/3: Creating SuperAdmin user '%s'...", sa_username)

        sa_user = AdminUser(
            username=sa_username,
            password_hash=hash_password(sa_password),
            is_superadmin=True,
            is_active=True,
        )
        sa_user.roles.append(sa_role)
        db.add(sa_user)
        db.commit()

        logger.info("=" * 60)
        logger.info("SUCCESS: SuperAdmin '%s' created.", sa_username)
        logger.info("  - is_superadmin: True (absolute RBAC bypass)")
        logger.info("  - Role: SuperAdmin (%d permissions)", len(ATOM_PERMISSIONS))
        logger.info("  - IMPORTANT: Change the default password immediately!")
        logger.info("=" * 60)

    except Exception:
        db.rollback()
        logger.exception("FATAL: Database initialization failed.")
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
