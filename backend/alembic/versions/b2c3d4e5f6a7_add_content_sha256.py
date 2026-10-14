"""Add content_sha256 column and index to documents table (Issue #31)

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-10-14 18:30:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('documents', sa.Column('content_sha256', sa.String(length=64), nullable=True))
    op.create_index('ix_documents_owner_id_content_sha256', 'documents', ['owner_id', 'content_sha256'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_documents_owner_id_content_sha256', table_name='documents')
    op.drop_column('documents', 'content_sha256')
