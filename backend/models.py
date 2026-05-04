from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Boolean, JSON, Index
from sqlalchemy.sql import func
from database import Base

class Card(Base):
    __tablename__ = "cards"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    set_name = Column(String)
    card_number = Column(String)
    rarity = Column(String)
    condition = Column(String)
    quantity = Column(Integer, default=1)
    purchase_price = Column(Float, nullable=True)
    current_price = Column(Float, nullable=True)
    last_updated = Column(DateTime(timezone=True), server_default=func.now())
    last_price_update = Column(DateTime(timezone=True), server_default=func.now())
    is_foil = Column(Boolean, default=False)
    is_signed = Column(Boolean, default=False)
    game = Column(String)  # magic, pokemon, yugioh
    notes = Column(String, nullable=True)
    price_sources = Column(JSON, nullable=True)
    # Catalog linkage: when present, refresh uses the source catalog API for an
    # authoritative TCGplayer price instead of fuzzy name matching.
    external_source = Column(String, nullable=True)  # 'scryfall'|'pokemontcg'|'ygoprodeck'
    external_id = Column(String, nullable=True)
    image_url = Column(String, nullable=True)
    # TCGplayer product ID (separate from external_id). Stored when the card was
    # imported from a TCGplayer URL so refreshes can hit TCGplayer's product
    # details API directly — its marketPrice is per-printing, while YGOPRODeck
    # only carries card-wide aggregates and zero-data per-printing entries.
    tcgplayer_product_id = Column(String, nullable=True)

class SealedProduct(Base):
    __tablename__ = "sealed_products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    set_name = Column(String)
    product_type = Column(String)  # booster box, pack, etc.
    quantity = Column(Integer, default=1)
    purchase_price = Column(Float, nullable=True)
    current_price = Column(Float, nullable=True)
    last_updated = Column(DateTime(timezone=True), server_default=func.now())
    last_price_update = Column(DateTime(timezone=True), server_default=func.now())
    game = Column(String)  # magic, pokemon, yugioh
    notes = Column(String, nullable=True)
    price_sources = Column(JSON, nullable=True)
    external_source = Column(String, nullable=True)
    external_id = Column(String, nullable=True)
    image_url = Column(String, nullable=True)
    tcgplayer_product_id = Column(String, nullable=True)

class PriceHistory(Base):
    __tablename__ = "price_history"
    __table_args__ = (Index('idx_price_history_item', 'item_type', 'item_id'),)

    id = Column(Integer, primary_key=True, index=True)
    item_type = Column(String)  # 'card' or 'sealed'
    item_id = Column(Integer)
    source = Column(String)
    price = Column(Float)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
