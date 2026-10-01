"""calendar proposal session

Revision ID: c314b8a46019
Revises: ad68b38fe653
Create Date: 2026-10-01 07:59:13.431953

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c314b8a46019'
down_revision: Union[str, Sequence[str], None] = 'ad68b38fe653'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('calendar_proposals', sa.Column('session_id', sa.Uuid(), nullable=True))
    op.create_index(
        op.f('ix_calendar_proposals_session_id'), 'calendar_proposals', ['session_id']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_calendar_proposals_session_id'), table_name='calendar_proposals')
    op.drop_column('calendar_proposals', 'session_id')
