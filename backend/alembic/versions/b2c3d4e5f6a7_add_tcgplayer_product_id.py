"""add tcgplayer_product_id column to cards and sealed_products

Adds a per-row TCGplayer product ID so the refresh path can hit TCGplayer's
product details API for the authoritative marketPrice — needed for YGO
printings whose YGOPRODeck set_price is zero (Starlight Rare, Ghost Rare, etc).

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-04 16:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ("cards", "sealed_products"):
        op.add_column(
            table, sa.Column("tcgplayer_product_id", sa.String(), nullable=True)
        )


def downgrade() -> None:
    for table in ("cards", "sealed_products"):
        op.drop_column(table, "tcgplayer_product_id")
