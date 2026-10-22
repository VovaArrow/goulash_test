"""HTTP API сервиса. Все ответы — JSON, ошибки — {"detail": "<по-русски>"}."""
import sqlite3
import threading
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from . import db
from .ai.rules import RuleBasedExplainer, RuleBasedKPWriter, RuleBasedQueryParser
from .catalog import CATEGORIES, CITIES, CONDITIONS, EXAMPLES, MODES
from .models import (Channel, CompareResult, Criteria, KPDraft, KPRequest, KPStatus, Mode, Note,
                     Profile, SearchRequest, SearchResponse)
from .scoring import WEIGHTS, reliability_report
from .search import run_search, score_selected
from .seed import seed_db

router = APIRouter(prefix="/api")
parser = RuleBasedQueryParser()
writer = RuleBasedKPWriter()


class Store:
    """Одно соединение SQLite на приложение; запросы сериализуются замком."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.lock = threading.Lock()


def get_conn(request: Request):
    store: Store = request.app.state.store
    with store.lock:
        yield store.conn


def _today() -> date:
    return date.today()


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _supplier_or_404(conn, supplier_id: int):
    found = db.load_suppliers(conn, [supplier_id])
    if not found:
        raise HTTPException(404, "Поставщик не найден")
    return found[0]


@router.get("/meta")
def meta():
    return {
        "categories": [{"key": k, "label": v} for k, v in CATEGORIES.items()],
        "cities": [{"key": c.key, "name": c.name, "country": c.country, "lat": c.lat, "lon": c.lon} for c in CITIES.values()],
        "conditions": [{"key": k, "label": v.label} for k, v in CONDITIONS.items()],
        "modes": [{"key": k, "label": v} for k, v in MODES.items()],
        "weights": WEIGHTS,
        "examples": EXAMPLES,
    }


class ParseIn(BaseModel):
    text: str = Field(max_length=1000)


@router.post("/parse", response_model=Criteria)
def parse(body: ParseIn, conn=Depends(get_conn)):
    return parser.parse(body.text, default_city=db.get_profile(conn).city)


@router.post("/search", response_model=SearchResponse)
def search(req: SearchRequest, conn=Depends(get_conn)):
    today = _today()
    return run_search(conn, req, db.get_profile(conn), today, explainer=RuleBasedExplainer(today))


@router.get("/suppliers/{supplier_id}")
def supplier_detail(supplier_id: int, conn=Depends(get_conn)):
    s = _supplier_or_404(conn, supplier_id)
    return {"supplier": s, "notes": db.list_notes(conn, supplier_id), "reliability": reliability_report(s, _today())}


class NoteIn(BaseModel):
    author: str = Field(default="Я", max_length=100)
    text: str = Field(max_length=2000)

    @field_validator("text", "author")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Поле не может быть пустым")
        return v


@router.post("/suppliers/{supplier_id}/notes", response_model=Note)
def add_note(supplier_id: int, body: NoteIn, conn=Depends(get_conn)):
    _supplier_or_404(conn, supplier_id)
    return db.add_note(conn, supplier_id, body.author, body.text, _now())


class CompareIn(BaseModel):
    criteria: Criteria
    supplier_ids: list[int]
    mode: Mode = "balance"


@router.post("/compare", response_model=CompareResult)
def compare(body: CompareIn, conn=Depends(get_conn)):
    ids = list(dict.fromkeys(body.supplier_ids))
    if not 2 <= len(ids) <= 4:
        raise HTTPException(422, "Выберите от 2 до 4 поставщиков")
    today = _today()
    explainer = RuleBasedExplainer(today)
    items = score_selected(conn, ids, body.criteria, db.get_profile(conn), body.mode, today, explainer)
    if len(items) != len(ids):
        raise HTTPException(404, "Поставщик не найден")
    return explainer.compare(items, body.criteria)


class DraftIn(BaseModel):
    criteria: Criteria
    supplier_ids: list[int] = Field(min_length=1, max_length=20)


@router.post("/kp/draft", response_model=KPDraft)
def kp_draft(body: DraftIn, conn=Depends(get_conn)):
    suppliers = db.load_suppliers(conn, body.supplier_ids)
    if len(suppliers) != len(set(body.supplier_ids)):
        raise HTTPException(404, "Поставщик не найден")
    return writer.draft(db.get_profile(conn), body.criteria, suppliers)


class KPItem(BaseModel):
    supplier_id: int
    channel: Channel


class KPIn(BaseModel):
    items: list[KPItem] = Field(min_length=1, max_length=20)
    product_label: str = Field(min_length=1, max_length=200)


_CHANNEL_FIELD = {"email": "email", "whatsapp": "whatsapp", "telegram": "telegram"}


@router.post("/kp", response_model=list[KPRequest])
def kp_create(body: KPIn, conn=Depends(get_conn)):
    suppliers = {s.id: s for s in db.load_suppliers(conn, [i.supplier_id for i in body.items])}
    for item in body.items:
        s = suppliers.get(item.supplier_id)
        if s is None:
            raise HTTPException(404, "Поставщик не найден")
        field = _CHANNEL_FIELD.get(item.channel)
        if field and not getattr(s, field):
            raise HTTPException(422, f"У поставщика {s.name} нет контактов для этого канала")
    now = _now()
    ids = [db.create_kp(conn, i.supplier_id, body.product_label, i.channel, now) for i in body.items]
    return [db.get_kp(conn, rid, now) for rid in ids]


@router.get("/kp", response_model=list[KPRequest])
def kp_list(conn=Depends(get_conn)):
    return db.list_kp(conn, _now())


class KPPatch(BaseModel):
    status: KPStatus | None = None
    price_rub: float | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, max_length=500)


@router.patch("/kp/{request_id}", response_model=KPRequest)
def kp_update(request_id: int, body: KPPatch, conn=Depends(get_conn)):
    now = _now()
    if db.get_kp(conn, request_id, now) is None:
        raise HTTPException(404, "Запрос КП не найден")
    db.update_kp(conn, request_id, body.model_dump(exclude_unset=True), now)
    return db.get_kp(conn, request_id, now)


@router.get("/profile", response_model=Profile)
def profile_get(conn=Depends(get_conn)):
    return db.get_profile(conn)


class ProfileIn(BaseModel):
    company: str = Field(min_length=1, max_length=200)
    city: str
    address: str = Field(min_length=1, max_length=300)
    contact_name: str = Field(max_length=200)
    phone: str = Field(max_length=50)
    email: str = Field(max_length=200)
    points: int = Field(ge=1, le=100000)


@router.put("/profile", response_model=Profile)
def profile_put(body: ProfileIn, conn=Depends(get_conn)):
    if body.city not in CITIES:
        raise HTTPException(422, "Неизвестный город")
    current = db.get_profile(conn)
    if body.city == current.city and body.address == current.address:
        lat, lon = current.lat, current.lon
    else:
        # Внешнего геокодера нет: точка доставки — центр выбранного города.
        lat, lon = CITIES[body.city].lat, CITIES[body.city].lon
    profile = Profile(**body.model_dump(), lat=lat, lon=lon)
    db.save_profile(conn, profile)
    return profile


@router.post("/demo/reset")
def demo_reset(conn=Depends(get_conn)):
    seed_db(conn)
    return {"ok": True}

