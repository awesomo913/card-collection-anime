"""add catalog linkage columns to cards and sealed_products

Revision ID: a1b2c3d4e5f6
Revises: 306501e4286b
Create Date: 2026-04-30 21:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "306501e4286b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ("cards", "sealed_products"):
        op.add_column(table, sa.Column("external_source", sa.String(), nullable=True))
        op.add_column(table, sa.Column("external_id", sa.String(), nullable=True))
        op.add_column(table, sa.Column("image_url", sa.String(), nullable=True))
        op.create_index(
            f"idx_{table}_external", table, ["external_source", "external_id"], unique=False
        )


def downgrade() -> None:
    for table in ("cards", "sealed_products"):
        op.drop_index(f"idx_{table}_external", table_name=table)
        op.drop_column(table, "image_url")
        op.drop_column(table, "external_id")
        op.drop_column(table, "external_source")
