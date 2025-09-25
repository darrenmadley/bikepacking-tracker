"""add indexes for track_points performance

Revision ID: 2cc97c8d78b8
Revises: afa7c82bc09e
Create Date: 2025-09-24 13:20:34.560696

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "add_track_points_indexes"
down_revision = "afa7c82bc09e"  # <-- your current head; keep as-is if different
branch_labels = None
depends_on = None

def upgrade():
    # btree indexes for typical reads
    op.execute("CREATE INDEX IF NOT EXISTS idx_track_points_track_id ON track_points(track_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_track_points_track_id_ts ON track_points(track_id, ts)")
    # spatial index for map operations
    op.execute("CREATE INDEX IF NOT EXISTS idx_track_points_geom ON track_points USING GIST(geom)")

def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_track_points_geom")
    op.execute("DROP INDEX IF EXISTS idx_track_points_track_id_ts")
    op.execute("DROP INDEX IF EXISTS idx_track_points_track_id")
