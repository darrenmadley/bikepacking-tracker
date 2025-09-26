"""live_positions indexes for device/ts and geom

Revision ID: 435f27ebaeba
Revises: c1287ef43675
Create Date: 2025-09-26 10:08:01.071431

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '435f27ebaeba'
down_revision: Union[str, Sequence[str], None] = 'c1287ef43675'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
