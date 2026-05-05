"""backfill external_source=tcgplayer for rows with tcgplayer_product_id

Phase C migration: now that the URL paste resolver returns
``external_source="tcgplayer"`` directly, port any older row that was saved
through the YGOPRODeck/PokemonTCG branch over to the new convention.

The ``tcgplayer_product_id`` column was added by ``b2c3d4e5f6a7`` precisely
so this migration could exist — when it's set, the row originated from a
TCGplayer URL paste and the most accurate price source is TCGplayer's
product details API. Copying ``tcgplayer_product_id`` -> ``external_id`` and
setting ``external_source = "tcgplayer"`` makes the per-source refresh path
hit the right API directly without checking the legacy column on every tick.

Idempotent: WHERE filters out any row already in the new shape.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-05-04 17:30:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ("cards", "sealed_products"):
        op.execute(
            f"""
            UPDATE {table}
            SET external_source = 'tcgplayer',
                external_id = tcgplayer_product_id
            WHERE tcgplayer_product_id IS NOT NULL
              AND tcgplayer_product_id != ''
              AND (external_source IS NULL OR external_source != 'tcgplayer')
            """
        )


def downgrade() -> None:
    # No clean inverse — we don't know what each row's prior external_source was.
    # If you need to roll back, restore from a DB backup.
    pass
