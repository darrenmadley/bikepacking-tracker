"""merge provider_msg_id + track_points_indexes

Revision ID: c1287ef43675
Revises: add_track_points_indexes, add_provider_msg_id
Create Date: 2025-09-25 16:16:33.142923

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1287ef43675'
down_revision: Union[str, Sequence[str], None] = ('add_track_points_indexes', 'add_provider_msg_id')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
