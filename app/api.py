"""HTTP API сервиса. Все ответы — JSON, ошибки — {"detail": "<по-русски>"}."""
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from . import db
from .ai.rules import RuleBasedExplainer, RuleBasedKPWriter, RuleBasedQueryParser
from .catalog import CATEGORIES, CITIES, CONDITIONS, EXAMPLES, MODES, PRODUCTS
from .models import (Channel, CompareResult, Criteria, KPDraft, KPRequest, KPStatus, Mode, Note,
                     Profile, SearchRequest, SearchResponse)
from .scoring import WEIGHTS, price_part, reliability_report, score_supplier
from .search import run_search, score_selected
from .seed import seed_db
from .real_case import RealCase, load_real_case, make_draft

router = APIRouter(prefix="/api")
parser = RuleBasedQueryParser()
writer = RuleBasedKPWriter()


@router.get("/real-case", response_model=RealCase)
def real_case():
    return load_real_case()


class Store:
    """Путь к базе SQLite. Соединение открывается на каждый запрос (WAL, ожидание блокировки 10 с)."""

    def __init__(self, path: str):
        self.path = path


def get_conn(request: Request):
    store: Store = request.app.state.store
    conn = db.connect(getattr(request.state, "db_path", store.path))
    try:
        # Public demo visitors may keep a database created before collections existed.
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='collections'").fetchone():
            db.init_db(conn)
        yield conn
    finally:
        conn.close()


@router.post("/real-case/{supplier_id}/draft", response_model=KPDraft)
def real_case_draft(supplier_id: str, conn=Depends(get_conn)):
    supplier = next((s for s in load_real_case().suppliers if s.id == supplier_id), None)
    if supplier is None:
        raise HTTPException(404, "Поставщик реального сценария не найден")
    return make_draft(supplier, db.get_profile(conn))


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
        "products": [{"key": k, "name": p.name, "unit": p.unit} for k, p in PRODUCTS.items()],
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
    supplier_ids: list[int] = Field(max_length=20)
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
    result = explainer.compare(items, body.criteria)
    result.criteria = body.criteria
    return result


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
    criteria: Criteria | None = None


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
    ids = [db.create_kp(conn, i.supplier_id, body.product_label, i.channel, now, criteria=body.criteria) for i in body.items]
    return [db.get_kp(conn, rid, now) for rid in ids]


@router.get("/kp", response_model=list[KPRequest])
def kp_list(conn=Depends(get_conn)):
    return db.list_kp(conn, _now())


class KPPatch(BaseModel):
    status: KPStatus | None = None
    price_rub: float | None = Field(default=None, ge=0, le=1e12, allow_inf_nan=False)
    note: str | None = Field(default=None, max_length=500)


    @field_validator("status")
    @classmethod
    def status_not_null(cls, value):
        if value is None:
            raise ValueError("Статус не может быть пустым")
        return value


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



def criteria_key(c: Criteria) -> dict:
    return c.model_dump(exclude={"raw", "unparsed", "suggestions"})


class CompareQuotesIn(BaseModel):
    request_ids: list[int] = Field(min_length=2, max_length=4)
    mode: Mode = "balance"


@router.post("/kp/compare", response_model=CompareResult)
def compare_quotes(body: CompareQuotesIn, conn=Depends(get_conn)):
    import statistics
    rows = [db.get_kp(conn, rid, _now()) for rid in dict.fromkeys(body.request_ids)]
    if any(r is None for r in rows):
        raise HTTPException(404, "Запрос КП не найден")
    if len(rows) < 2 or len({r.supplier_id for r in rows}) != len(rows):
        raise HTTPException(422, "Выберите КП от разных поставщиков")
    if any(r.status != "received" or r.price_rub is None or r.criteria is None for r in rows):
        raise HTTPException(422, "Нужны полученные КП с ценами и сохранёнными критериями")
    c = rows[0].criteria
    if not c.product or any(criteria_key(r.criteria) != criteria_key(c) for r in rows):
        raise HTTPException(422, "Можно сравнить КП по одному товару с одинаковыми критериями")
    today = _today()
    explainer = RuleBasedExplainer(today)
    items = score_selected(conn, [r.supplier_id for r in rows], c, db.get_profile(conn), body.mode, today)
    by_id = {r.supplier_id: r for r in rows}
    median = statistics.median(r.price_rub for r in rows)
    for item in items:
        if item.offer is None:
            raise HTTPException(422, "У поставщика нет запрошенного товара")
        price = by_id[item.supplier.id].price_rub
        item.offer = item.offer.model_copy(update={"price": price, "price_rub": price})
        item.supplier = item.supplier.model_copy(update={"currency": "RUB"})
        item.score, item.parts = score_supplier(item.supplier, c, item.offer, item.distance_km,
                                                median, body.mode, today)
        item.reasons = explainer.reasons(item, c, median)
    result = explainer.compare(items, c)
    result.criteria = c
    result.quoted = True
    result.conclusion = "По ценам из полученных КП. " + result.conclusion
    return result


class QuoteTextIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


@router.post("/kp/{request_id}/extract")
def quote_extract(request_id: int, body: QuoteTextIn, conn=Depends(get_conn)):
    from .ai.quotes import extract_quote
    row = db.get_kp(conn, request_id, _now())
    if row is None:
        raise HTTPException(404, "Запрос КП не найден")
    result = extract_quote(body.text)
    if row.criteria and row.criteria.product:
        unit = PRODUCTS[row.criteria.product].unit
        if result.unit is not None and result.unit != unit:
            result.price_rub = None
            result.warning = f"В ответе цена за {result.unit}, а товар учитывается за {unit}. Уточните цену за нужную единицу."
    return result
