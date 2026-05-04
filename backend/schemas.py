from pydantic import BaseModel, ConfigDict
from typing import Optional, Dict
from datetime import datetime

class CardBase(BaseModel):
    name: str
    set_name: str
    card_number: Optional[str] = None
    rarity: Optional[str] = None
    condition: Optional[str] = None
    quantity: int = 1
    purchase_price: Optional[float] = None
    is_foil: bool = False
    is_signed: bool = False
    game: str  # magic, pokemon, yugioh
    price_sources: Optional[Dict[str, float]] = None
    notes: Optional[str] = None
    external_source: Optional[str] = None
    external_id: Optional[str] = None
    image_url: Optional[str] = None
    tcgplayer_product_id: Optional[str] = None

class CardCreate(CardBase):
    pass

class CardUpdate(BaseModel):
    name: Optional[str] = None
    set_name: Optional[str] = None
    card_number: Optional[str] = None
    rarity: Optional[str] = None
    condition: Optional[str] = None
    quantity: Optional[int] = None
    purchase_price: Optional[float] = None
    current_price: Optional[float] = None
    is_foil: Optional[bool] = None
    is_signed: Optional[bool] = None
    game: Optional[str] = None
    notes: Optional[str] = None
    external_source: Optional[str] = None
    external_id: Optional[str] = None
    image_url: Optional[str] = None
    tcgplayer_product_id: Optional[str] = None

class Card(CardBase):
    id: int
    current_price: Optional[float] = None
    price_sources: Optional[Dict[str, float]] = None
    last_updated: datetime
    image_url: Optional[str] = None
    external_source: Optional[str] = None
    external_id: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

class SealedProductBase(BaseModel):
    name: str
    set_name: str
    product_type: str  # booster box, pack, etc.
    quantity: int = 1
    purchase_price: Optional[float] = None
    game: str  # magic, pokemon, yugioh
    notes: Optional[str] = None
    price_sources: Optional[Dict[str, float]] = None
    external_source: Optional[str] = None
    external_id: Optional[str] = None
    image_url: Optional[str] = None
    tcgplayer_product_id: Optional[str] = None

class SealedProductCreate(SealedProductBase):
    pass

class SealedProductUpdate(BaseModel):
    name: Optional[str] = None
    set_name: Optional[str] = None
    product_type: Optional[str] = None
    quantity: Optional[int] = None
    purchase_price: Optional[float] = None
    current_price: Optional[float] = None
    game: Optional[str] = None
    notes: Optional[str] = None
    external_source: Optional[str] = None
    external_id: Optional[str] = None
    image_url: Optional[str] = None
    tcgplayer_product_id: Optional[str] = None

class SealedProduct(SealedProductBase):
    id: int
    current_price: Optional[float] = None
    price_sources: Optional[Dict[str, float]] = None
    last_updated: datetime
    image_url: Optional[str] = None
    external_source: Optional[str] = None
    external_id: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class BackupExportRequest(BaseModel):
    password: str


class BackupImportRequest(BaseModel):
    password: str
    encrypted: str
    replace: bool = True


class CatalogResult(BaseModel):
    """One row of catalog search output, normalized across game-specific APIs."""
    external_source: str
    external_id: str
    name: str
    set_name: str
    image_url: Optional[str] = None
    tcgplayer_price: Optional[float] = None
    tcgplayer_price_foil: Optional[float] = None
    rarity: Optional[str] = None
    # When the lookup went through TCGplayer (URL paste resolver), the underlying
    # product ID. Frontend forwards this on save so refresh can hit TCGplayer's
    # product details API directly for an authoritative per-printing price.
    tcgplayer_product_id: Optional[str] = None
