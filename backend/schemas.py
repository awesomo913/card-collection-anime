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

class Card(CardBase):
    id: int
    current_price: Optional[float] = None
    price_sources: Optional[Dict[str, float]] = None
    last_updated: datetime
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

class SealedProduct(SealedProductBase):
    id: int
    current_price: Optional[float] = None
    price_sources: Optional[Dict[str, float]] = None
    last_updated: datetime
    model_config = ConfigDict(from_attributes=True)
