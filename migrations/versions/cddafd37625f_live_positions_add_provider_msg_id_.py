"""live_positions: add provider_msg_id + unique index

Revision ID: cddafd37625f
Revises: add_track_points_indexes
Create Date: 2025-09-25 15:51:25.762090

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers:
revision = "add_provider_msg_id"
down_revision = "afa7c82bc09e"  # e.g., afa7c82bc09e
branch_labels = None
depends_on = None

def upgrade():
    op.execute("""
        ALTER TABLE live_positions
        ADD COLUMN IF NOT EXISTS provider_msg_id varchar(64)
    """)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE schemaname='public' AND indexname='uq_live_positions_msgid'
            ) THEN
                CREATE UNIQUE INDEX uq_live_positions_msgid
                    ON live_positions(provider_msg_id)
                 WHERE provider_msg_id IS NOT NULL;
            END IF;
        END $$;
    """)

def downgrade():
    op.execute("DROP INDEX IF EXISTS uq_live_positions_msgid;")
    op.execute("ALTER TABLE live_positions DROP COLUMN IF EXISTS provider_msg_id;")
