"""Единые типы данных сервиса: их используют БД, поиск, тексты и API."""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

Condition = Literal[
    "declarations", "mercury", "haccp", "halal", "chestny_znak",
    "own_delivery", "cold_chain", "deferral", "producer_only",
]
Mode = Literal["balance", "reliability", "price", "speed"]
SortKey = Literal["score", "price", "distance"]
Level = Literal["ok", "warn", "bad"]
Channel = Literal["email", "whatsapp", "telegram", "copy"]
KPStatus = Literal["sent", "waiting", "received", "declined"]


class Criteria(BaseModel):
    raw: str = ""
    category: str | None = None
    product: str | None = None
    city: str | None = None
    radius_km: int = 300
    volume_kg_month: float | None = None
    max_price_rub: float | None = None
    required: list[Condition] = []
    preferred: list[Condition] = []
    deferral_days: int | None = None
    unparsed: list[str] = []
    suggestions: list[Condition] = []


class Filters(BaseModel):
    max_distance_km: int | None = None
    fit_min_order: bool = False
    hide_critical: bool = True
    supplier_type: Literal["any", "producer", "distributor"] = "any"


class SearchRequest(BaseModel):
    criteria: Criteria
    filters: Filters = Filters()
    mode: Mode = "balance"
    sort: SortKey = "score"


class Product(BaseModel):
    name: str
    key: str
    category: str
    price: float | None
    price_rub: float | None
    unit: str


class Document(BaseModel):
    kind: Literal["declaration", "certificate", "mercury", "haccp", "chestny_znak", "halal"]
    number: str | None
    valid_until: date | None


class Supplier(BaseModel):
    id: int
    name: str
    legal_name: str
    inn: str
    country: Literal["RU", "KZ"]
    city: str
    address: str
    lat: float
    lon: float
    type: Literal["producer", "distributor"]
    founded: date
    employees: int
    revenue_mln_rub: float | None
    phone: str | None
    email: str | None
    website: str | None
    whatsapp: str | None
    telegram: str | None
    preferred_channel: Literal["whatsapp", "email", "telegram"] | None
    min_order_kg: float
    own_delivery: bool
    cold_chain: bool
    delivery_days: int
    delivery_radius_km: int
    delivery_schedule: str | None
    deferral_days: int | None
    prepay_required: bool
    currency: Literal["RUB", "KZT"]
    rating: float | None
    reviews_count: int
    lawsuits: int
    tax_debt_rub: float
    bankruptcy: bool
    mass_address: bool
    linked_problem_company: str | None
    data_as_of: date
    sources: dict[str, str]
    categories: list[str]
    products: list[Product]
    documents: list[Document]


class Flag(BaseModel):
    level: Level
    text: str
    hint: str | None = None


class ScoreParts(BaseModel):
    match: int
    reliability: int
    price: int
    logistics: int


class ScoredSupplier(BaseModel):
    supplier: Supplier
    score: int
    parts: ScoreParts
    distance_km: float
    offer: Product | None
    flags: list[Flag]
    reasons: list[str] = []
    logistics_text: str = ""


class HiddenReason(BaseModel):
    reason: str
    count: int


class RelaxHint(BaseModel):
    condition: Condition
    label: str
    extra: int


class SearchResponse(BaseModel):
    criteria: Criteria
    results: list[ScoredSupplier]
    hidden: list[HiddenReason]
    median_price_rub: float | None
    origin: dict
    facets: dict[str, int] = {}
    relax: list[RelaxHint] = []


class FactGroup(BaseModel):
    title: str
    level: Level
    facts: list[Flag]


class ReliabilityReport(BaseModel):
    verdict: Literal["high", "medium", "low"]
    counts: dict[str, int]
    groups: list[FactGroup]
    as_of: date


class Profile(BaseModel):
    company: str = Field(min_length=1, max_length=200)
    city: str
    address: str = Field(min_length=1, max_length=300)
    lat: float
    lon: float
    contact_name: str = Field(max_length=200)
    phone: str = Field(max_length=50)
    email: str = Field(max_length=200)
    points: int = Field(ge=1, le=100000)


class Note(BaseModel):
    id: int
    supplier_id: int
    author: str
    text: str
    created_at: datetime


class KPEvent(BaseModel):
    at: datetime
    status: KPStatus
    text: str


class KPRequest(BaseModel):
    id: int
    supplier_id: int
    supplier_name: str
    product_label: str
    channel: Channel
    status: KPStatus
    price_rub: float | None
    note: str | None
    created_at: datetime
    days_waiting: int
    reply_due: date
    events: list[KPEvent] = []


class KPDraft(BaseModel):
    subject: str
    body: str


class CompareResult(BaseModel):
    items: list[ScoredSupplier]
    best: dict[str, int]
    conclusion: str
