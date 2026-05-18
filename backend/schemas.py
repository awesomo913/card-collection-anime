from pydantic import BaseModel, ConfigDict, Field
from typing import List, Literal, Optional, Dict
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
    # Market price snapshot at acquisition (auto-set by create; user can override
    # via PATCH if they had a different starting baseline in mind).
    acquired_price: Optional[float] = None

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
    acquired_price: Optional[float] = None

class Card(CardBase):
    id: int
    current_price: Optional[float] = None
    price_sources: Optional[Dict[str, float]] = None
    last_updated: datetime
    created_at: Optional[datetime] = None
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
    acquired_price: Optional[float] = None

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
    acquired_price: Optional[float] = None

class SealedProduct(SealedProductBase):
    id: int
    current_price: Optional[float] = None
    price_sources: Optional[Dict[str, float]] = None
    last_updated: datetime
    created_at: Optional[datetime] = None
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


# ----- /identify endpoints (DeepSeek multimodal) ---------------------------

class IdentifyCandidate(BaseModel):
    """One ranked guess from the multimodal identifier.

    The frontend renders these as buttons under each uploaded image. Clicking
    `Use TCGplayer URL` pipes ``suggested_urls[0]`` into the existing
    /catalog/resolve flow; clicking `Try search query` pipes
    ``search_queries[0]`` into the existing /catalog/search flow.
    """
    game: Literal["magic", "pokemon", "yugioh", "unknown"]
    name: str
    set_name: Optional[str] = None
    printing_notes: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    justification: str = ""
    suggested_urls: List[str] = []
    search_queries: List[str] = []


class IdentifyResult(BaseModel):
    """Per-image outcome from one DeepSeek call.

    ``error`` is set when the call (or the model's JSON parse) failed for
    THIS image specifically. Callers see a partial-success batch instead of
    a 500.
    """
    source_filename: str
    candidates: List[IdentifyCandidate] = []
    error: Optional[str] = None


class IdentifyBatchResponse(BaseModel):
    """Wrapper around N IdentifyResults plus the total wall-clock duration."""
    results: List[IdentifyResult]
    duration_seconds: float


# ----- /forecast endpoints (DeepSeek text-only) ----------------------------
# Speculative projections from an LLM given price history + metadata. Not
# investment advice — the frontend renders a prominent disclaimer.

class ForecastHorizon(BaseModel):
    """One time-horizon projection (typically 7d / 30d / 90d returned together).

    ``target`` is the model's point estimate; ``low``/``high`` bracket the
    plausible range. ``confidence`` is the model's own self-rated confidence
    (0..1) — useful as a tiebreaker, not as a hard probability.
    """
    days: int = Field(ge=1, le=730)
    low: float = Field(ge=0)
    high: float = Field(ge=0)
    target: float = Field(ge=0)
    confidence: float = Field(ge=0.0, le=1.0)


class ForecastResult(BaseModel):
    """Per-item forecast envelope. Cached server-side; resends on data change."""
    item_type: Literal["card", "sealed"]
    item_id: int
    item_name: str
    current_price: Optional[float] = None
    horizons: List[ForecastHorizon] = []
    direction: Literal["up", "down", "flat", "unknown"] = "unknown"
    reasoning: str = ""
    drivers: List[str] = []
    caveats: List[str] = []
    generated_at: datetime
    model: str
    history_samples_used: int = 0
    cached: bool = False  # True when served from in-memory cache


# ----- /forecast/batch (whole-collection sweep) -----------------------------

class BatchForecastItem(BaseModel):
    """One item the caller wants forecast in a batch."""
    type: Literal["card", "sealed"]
    id: int


class BatchForecastRequest(BaseModel):
    """POST /forecast/batch body. Server caps total ``items`` length."""
    items: List[BatchForecastItem]


class BatchForecastResultRow(BaseModel):
    """Per-item result envelope inside a batch response.

    ``forecast`` may be None when the item is missing, has no history, or
    DeepSeek returned an error for THIS item — the batch never 500s on
    one bad row, the row just carries ``error``.
    """
    type: Literal["card", "sealed"]
    id: int
    name: str = "(unknown)"
    qty: int = 0
    current_price: Optional[float] = None
    forecast: Optional[ForecastResult] = None
    error: Optional[str] = None


class AggregateHorizon(BaseModel):
    """Portfolio-wide projection for one time horizon.

    Sum of (qty × horizon.low/target/high) across items whose forecast cleared
    the confidence floor (0.3). Items below the floor contribute current_price
    × qty in all three columns and count as skipped — keeps the totals honest
    when the model wasn't sure.
    """
    days: int
    current_total: float
    projected_low: float
    projected_target: float
    projected_high: float
    confidence_weighted_target: float
    items_included: int
    items_skipped: int


class BatchForecastResponse(BaseModel):
    """Wrapper around N item results + the portfolio roll-up."""
    results: List[BatchForecastResultRow]
    aggregate: List[AggregateHorizon]
    duration_seconds: float
    cache_hits: int
    cache_misses: int
    model: str
