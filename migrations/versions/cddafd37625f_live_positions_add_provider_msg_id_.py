"""live_positions: add provider_msg_id + unique index

Revision ID: cddafd37625f
Revises: add_track_points_indexes
Create Date: 2025-09-25 15:51:25.762090

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
import json
from datetime import timedelta


# revision identifiers:
revision = "<auto_generated_by_alembic>"
down_revision = "c1287ef43675"
branch_labels = None
depends_on = None

def upgrade():
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_live_positions_device_ts
        ON live_positions (device_id, ts DESC);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_live_positions_geom
        ON live_positions USING GIST (geom);
    """)

def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_live_positions_geom;")
    op.execute("DROP INDEX IF EXISTS idx_live_positions_device_ts;")