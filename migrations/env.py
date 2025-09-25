# migrations/env.py
from logging.config import fileConfig
import logging
import os
import sys
from configparser import ConfigParser

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine.url import make_url

# --- Make sure 'app/' is importable when running Alembic from project root ---
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))              # .../migrations
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, os.pardir))  # project root
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# --- Import your models' metadata (adjust if your Base lives elsewhere) ---
try:
    from app.models import Base
except Exception as e:
    raise RuntimeError(
        "Failed to import app.models.Base. Ensure 'app' is a package and models import works."
    ) from e

# Alembic Config object, provides access to values within the active ini file
config = context.config

# Set up logging if the ini file contains logging sections
if config.config_file_name:
    try:
        cp = ConfigParser()
        cp.read(config.config_file_name)
        if cp.has_section("formatters"):
            fileConfig(config.config_file_name, disable_existing_loggers=False)
    except Exception:
        # Don't fail migrations if a minimal local ini lacks logging config
        pass


def resolve_database_url() -> str:
    """
    Resolution order:
      1) DATABASE_URL environment variable
      2) sqlalchemy.url in the active ini (-c some.ini or default alembic.ini)
      3) sqlalchemy.url in ./alembic.local.ini (optional, kept out of git)
    """
    env_url = os.getenv("DATABASE_URL")
    if env_url:
        return env_url

    ini_url = config.get_main_option("sqlalchemy.url")
    if ini_url:
        return ini_url

    # Optional local override file at project root
    local_path = os.path.join(PROJECT_ROOT, "alembic.local.ini")
    if os.path.exists(local_path):
        cp = ConfigParser()
        cp.read(local_path)
        if cp.has_option("alembic", "sqlalchemy.url"):
            return cp.get("alembic", "sqlalchemy.url")

    raise RuntimeError(
        "No database URL configured. Set DATABASE_URL env var, "
        "or set [alembic] sqlalchemy.url in your ini, "
        "or provide an alembic.local.ini."
    )


DATABASE_URL = resolve_database_url()

# Log a sanitized URL (mask password)
try:
    safe_url = make_url(DATABASE_URL).set(password="***")
except Exception:
    safe_url = DATABASE_URL
logging.getLogger("alembic.env").info("Using database URL: %s", safe_url)

# Use your models' metadata for autogenerate
target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):
    """Skip system tables in autogenerate (keep alembic_version)."""
    if type_ == "table" and name in {"spatial_ref_sys"}:
        return False
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        include_object=include_object,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = create_engine(
        DATABASE_URL,
        poolclass=pool.NullPool,  # no pooling during migrations
        future=True,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
