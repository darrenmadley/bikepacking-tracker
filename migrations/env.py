from logging.config import fileConfig
import os
import sys
import logging

from alembic import context
from sqlalchemy import create_engine, pool

# --- Make sure 'app/' is importable when running alembic from project root ---
sys.path.append(os.path.dirname(os.path.abspath(__file__)))              # /migrations
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # project root

# Import your models' metadata
from app.models import Base  # adjust path if your models live elsewhere

# Alembic Config object, provides access to values within alembic.ini
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Prefer env var, else alembic.ini
DATABASE_URL = os.getenv("DATABASE_URL") or config.get_main_option("sqlalchemy.url")
logging.getLogger("alembic.env").warning(f"USING DATABASE_URL={DATABASE_URL}")


# Use your models' metadata for autogenerate
target_metadata = Base.metadata

# Optional: skip PostGIS and other extension/system tables
def include_object(object, name, type_, reflected, compare_to):
    system_tables = {"spatial_ref_sys"}  # add more if needed
    if type_ == "table" and name in system_tables:
        return False
    return True

def run_migrations_offline():
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online():
    connectable = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,   # <-- keep metadata here
            compare_type=True,
            include_object=include_object,
        )
        with context.begin_transaction():_

