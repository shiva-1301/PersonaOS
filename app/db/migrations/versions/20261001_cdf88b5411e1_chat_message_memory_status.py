"""chat message memory status

Revision ID: cdf88b5411e1
Revises: c8e2a745bec9
Create Date: 2026-10-01 02:08:01.718074

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cdf88b5411e1'
down_revision: Union[str, Sequence[str], None] = 'c8e2a745bec9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('chat_messages', sa.Column('memory_status', sa.String(length=16), nullable=True))
    # Autogenerate does not detect CHECK constraints; added by hand.
    op.create_check_constraint(
        op.f('ck_chat_messages_memory_status'),
        'chat_messages',
        "memory_status IN ('pending', 'done', 'failed')",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f('ck_chat_messages_memory_status'), 'chat_messages', type_='check')
    op.drop_column('chat_messages', 'memory_status')
