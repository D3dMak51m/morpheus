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
    from app.models import AdminUser, Role, RolePermission, ScrapingLandscape, AgentProfile
    from app.rbac import ATOM_PERMISSIONS, hash_password

    logger.info("=" * 60)
    logger.info("DAEDALUS Database Initialization")
    logger.info("=" * 60)

    # Step 1: Create all tables
    logger.info("Step 1/5: Creating database tables...")
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
        else:
            logger.info("Step 2/5: Creating SuperAdmin role with %d atom permissions...", len(ATOM_PERMISSIONS))

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
            logger.info("Step 3/5: Creating SuperAdmin user '%s'...", sa_username)

            sa_user = AdminUser(
                username=sa_username,
                password_hash=hash_password(sa_password),
                is_superadmin=True,
                is_active=True,
            )
            sa_user.roles.append(sa_role)
            db.add(sa_user)
            db.commit()

            logger.info("SUCCESS: SuperAdmin '%s' created.", sa_username)

        # Step 4: Seed scraping landscape
        logger.info("Step 4/5: Seeding scraping landscape targets...")
        default_targets = [
            {"platform": "telegram", "target_identifier": "@tashkent_news", "associated_tags": ["news", "tashkent"]},
            {"platform": "telegram", "target_identifier": "@uzbekistan_live", "associated_tags": ["news", "uzbekistan"]},
            {"platform": "web", "target_identifier": "https://kun.uz/en", "associated_tags": ["news", "uzbekistan"]},
        ]
        for t in default_targets:
            existing = db.query(ScrapingLandscape).filter(
                ScrapingLandscape.target_identifier == t["target_identifier"]
            ).first()
            if not existing:
                db.add(ScrapingLandscape(
                    platform=t["platform"],
                    target_identifier=t["target_identifier"],
                    is_active=True,
                    associated_tags=t["associated_tags"],
                ))
                logger.info("  Seeded target: %s (%s)", t["target_identifier"], t["platform"])
            else:
                logger.info("  Target '%s' already exists — skipping.", t["target_identifier"])
        db.commit()

        # Step 5: Migrate YAML profiles to agent_profiles table
        logger.info("Step 5/5: Migrating YAML profiles to database...")
        _migrate_yaml_profiles(db)
        db.commit()

        logger.info("=" * 60)
        logger.info("Database initialization complete.")
        logger.info("=" * 60)

    except Exception:
        db.rollback()
        logger.exception("FATAL: Database initialization failed.")
        sys.exit(1)
    finally:
        db.close()


def _migrate_yaml_profiles(db) -> None:
    """Read YAML personality files and insert them into agent_profiles if not already present."""
    import yaml
    from app.models import AgentProfile

    config_dir = os.getenv("GLOBAL_CONFIG_DIR", "/app/global_config/personalities")
    if not os.path.exists(config_dir):
        logger.warning("Config directory %s not found. Skipping YAML migration.", config_dir)
        return

    for filename in os.listdir(config_dir):
        if not (filename.endswith(".yaml") or filename.endswith(".yml")):
            continue

        filepath = os.path.join(config_dir, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)

            agent_id = data.get("agent_id")
            if not agent_id:
                continue

            existing = db.query(AgentProfile).filter(AgentProfile.agent_id == agent_id).first()
            if existing:
                logger.info("  Profile for agent %s already exists — skipping.", agent_id)
                continue

            identity = data.get("identity", {})
            personality = data.get("personality", {})
            interests = data.get("interests", {})
            rules = data.get("behavioral_rules", {})

            profile = AgentProfile(
                agent_id=agent_id,
                codename=data.get("codename", agent_id),
                caste=data.get("caste", "alpha"),
                full_name=identity.get("full_name", "Unknown"),
                residence_city=identity.get("city"),
                residence_state=identity.get("country"),
                nationality=identity.get("country"),
                profession=identity.get("occupation"),
                education=identity.get("education"),
                spoken_languages=["ru", "uz"],
                core_interests=interests.get("primary", []) + interests.get("secondary", []),
                communication_style=personality,
                behavioral_rules=rules if isinstance(rules, dict) else {},
                platforms=data.get("platforms", []),
                layers_affinity=data.get("layers_affinity", {}),
                active_hours_start=8,
                active_hours_end=22,
            )
            db.add(profile)
            logger.info("  Migrated profile for agent %s (%s)", agent_id, data.get("codename"))

        except Exception as e:
            logger.error("  Failed to migrate %s: %s", filename, e)


if __name__ == "__main__":
    main()
