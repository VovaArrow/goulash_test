"""Подборки вокруг потребности: снимок выбранных компаний и полученные ответы."""

import hashlib
import json
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from . import db
from .catalog import CATEGORIES, CITIES, PRODUCTS
from .models import Criteria, KPDraft, SearchRequest
from .real_case import load_real_case, make_draft
from .scoring import best_offer
from .ai.rules import RuleBasedKPWriter


class SaveCollection(BaseModel):
    search_key: UUID
    source: Literal["demo", "real"] = "demo"
    search: SearchRequest | None = None
    supplier_ids: list[str] = Field(max_length=20)


class MemberPatch(BaseModel):
    status: Literal["selected", "sent", "received"] | None = None
    price_rub: float | None = Field(default=None, ge=0, le=1e12, allow_inf_nan=False)
    terms: str | None = Field(default=None, max_length=2000)
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("status", "terms", "note")
    @classmethod
    def not_null(cls, value):
        if value is None:
            raise ValueError("Поле не может быть пустым")
        return value


class CollectionCompare(BaseModel):
    item_ids: list[int] = Field(min_length=2, max_length=4)
    kind: Literal["suppliers", "offers"]


def now():
    return datetime.now().replace(microsecond=0).isoformat()


def title_for(c):
    return (
        PRODUCTS[c.product].name
        if c.product
        else CATEGORIES.get(c.category, c.raw[:80] or "Подбор поставщиков")
    )


def normalized_search(search):
    data = search.model_dump()
    for key in ("raw", "unparsed", "suggestions"):
        data["criteria"].pop(key, None)
    for key in ("required", "preferred"):
        data["criteria"][key] = sorted(data["criteria"][key])
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def real_search():
    return SearchRequest(
        criteria=Criteria(
            product="mozzarella",
            city="ekb",
            volume_kg_month=200,
            raw=load_real_case().query,
            required=["declarations", "cold_chain"],
            preferred=["deferral"],
            deferral_days=14,
        )
    )


def snapshot(conn, source, key, criteria):
    if source == "real":
        supplier = next((s for s in load_real_case().suppliers if s.id == key), None)
        if supplier is None:
            raise HTTPException(404, "Компания реального примера не найдена")
        return dict(
            key=key,
            name=supplier.name,
            unit="кг",
            phone=supplier.phone,
            email=supplier.email,
            preferred_channel="email" if supplier.email else "copy",
            facts=[f.model_dump() for f in supplier.company_facts + supplier.facts],
            sources=[s.model_dump(mode="json") for s in supplier.sources],
            whatsapp=None,
            telegram=None,
        )
    try:
        sid = int(key)
    except ValueError:
        raise HTTPException(422, "Некорректный поставщик")
    suppliers = db.load_suppliers(conn, [sid])
    if not suppliers:
        raise HTTPException(404, "Поставщик не найден")
    s = suppliers[0]
    p = best_offer(s, criteria)
    if (criteria.product or criteria.category) and p is None:
        raise HTTPException(422, "Поставщик не соответствует товару подборки")
    price = (
        f"{p.price_rub:g} ₽/{p.unit}"
        if p and p.price_rub is not None
        else "Цена по запросу"
    )
    values = [
        ("product", "Товар", p.name if p else "Ассортимент поставщика"),
        ("price", "Цена в каталоге", price),
        ("min_order", "Минимальная партия", f"{s.min_order_kg:g} кг"),
        (
            "delivery",
            "Доставка",
            f"{s.delivery_days} дн., "
            + ("своя доставка" if s.own_delivery else "самовывоз / ТК"),
        ),
        (
            "documents",
            "Документы",
            ", ".join(
                {
                    "declaration": "Декларация",
                    "certificate": "Сертификат",
                    "mercury": "Меркурий",
                    "haccp": "ХАССП",
                    "halal": "Халяль",
                    "chestny_znak": "Честный знак",
                }[d.kind]
                + (f" до {d.valid_until}" if d.valid_until else "")
                for d in s.documents
            )
            or "Нет данных",
        ),
        (
            "deferral",
            "Отсрочка",
            f"{s.deferral_days} дн." if s.deferral_days else "Не подтверждена",
        ),
    ]
    return dict(
        key=str(s.id),
        name=s.name,
        unit=p.unit if p else None,
        phone=s.phone,
        email=s.email,
        whatsapp=s.whatsapp,
        telegram=s.telegram,
        preferred_channel=s.preferred_channel or "copy",
        facts=[
            dict(key=k, label=label, value=value, source_ids=[])
            for k, label, value in values
        ],
        sources=[],
    )


def get_collection(conn, collection_id):
    row = conn.execute(
        "SELECT * FROM collections WHERE id=?", (collection_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Подборка не найдена")
    search = SearchRequest.model_validate_json(row["search_request"])
    members = conn.execute(
        "SELECT * FROM collection_members WHERE collection_id=? ORDER BY id",
        (collection_id,),
    ).fetchall()
    items = [
        dict(
            id=r["id"],
            supplier=json.loads(r["snapshot"]),
            status=r["status"],
            price_rub=r["price_rub"],
            terms=r["terms"],
            note=r["note"],
            migrated=r["legacy_kp_id"] is not None,
        )
        for r in members
    ]
    c = search.criteria
    return dict(
        id=row["id"],
        source=row["source"],
        title=row["title"],
        search=search.model_dump(mode="json"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        city=CITIES[c.city].name if c.city else "Город не сохранён",
        unit=PRODUCTS[c.product].unit if c.product else None,
        quote_comparable=bool(c.product),
        items=items,
        selected_count=len(items),
        sent_count=sum(i["status"] == "sent" for i in items),
        received_count=sum(i["status"] == "received" for i in items),
    )


def import_legacy(conn):
    """Идемпотентный перенос старых КП; исходные строки и история остаются в БД."""
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT k.* FROM kp_requests k LEFT JOIN collection_members m ON m.legacy_kp_id=k.id WHERE m.id IS NULL ORDER BY k.created_at,k.id"
        ).fetchall()
        for r in rows:
            c = (
                Criteria.model_validate_json(r["criteria"])
                if r["criteria"]
                else Criteria(raw=r["product_label"])
            )
            search = SearchRequest(criteria=c)
            identity = (
                normalized_search(search) if r["criteria"] else f"unknown:{r['id']}"
            )
            key = "legacy:" + hashlib.sha256(identity.encode()).hexdigest()
            collection = conn.execute(
                "SELECT id FROM collections WHERE search_key=?", (key,)
            ).fetchone()
            if (
                collection
                and conn.execute(
                    "SELECT 1 FROM collection_members WHERE collection_id=? AND supplier_key=?",
                    (collection["id"], str(r["supplier_id"])),
                ).fetchone()
            ):
                key += ":" + str(r["id"])
                collection = conn.execute(
                    "SELECT id FROM collections WHERE search_key=?", (key,)
                ).fetchone()
            if collection is None:
                cid = conn.execute(
                    "INSERT INTO collections(search_key,source,title,search_request,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                    (
                        key,
                        "demo",
                        title_for(c),
                        search.model_dump_json(),
                        r["created_at"],
                        r["created_at"],
                    ),
                ).lastrowid
            else:
                cid = collection["id"]
            data = snapshot(conn, "demo", str(r["supplier_id"]), c)
            status = {"received": "received", "sent": "sent", "waiting": "sent"}.get(
                r["status"], "selected"
            )
            note = r["note"] or ""
            if r["status"] == "declined":
                note = "Ранее поставщик отказал. " + note
            conn.execute(
                "INSERT INTO collection_members(collection_id,supplier_key,snapshot,status,price_rub,note,legacy_kp_id) VALUES(?,?,?,?,?,?,?)",
                (
                    cid,
                    str(r["supplier_id"]),
                    json.dumps(data, ensure_ascii=False),
                    status,
                    r["price_rub"],
                    note,
                    r["id"],
                ),
            )


def build_router(get_conn):
    router = APIRouter(prefix="/api/collections")

    @router.get("")
    def list_collections(conn=Depends(get_conn)):
        import_legacy(conn)
        ids = conn.execute(
            "SELECT id FROM collections ORDER BY updated_at DESC,id DESC"
        ).fetchall()
        return [get_collection(conn, row["id"]) for row in ids]

    @router.post("")
    def save(body: SaveCollection, conn=Depends(get_conn)):
        search = real_search() if body.source == "real" else body.search
        if search is None or search.criteria.location_unresolved:
            raise HTTPException(422, "Сначала уточните критерии поиска")
        keys = list(dict.fromkeys(body.supplier_ids))
        loaded = [snapshot(conn, body.source, key, search.criteria) for key in keys]
        snapshots = {data["key"]: data for data in loaded}
        keys = list(snapshots)
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM collections WHERE search_key=?", (str(body.search_key),)
            ).fetchone()
            if row:
                if row["source"] != body.source or normalized_search(
                    SearchRequest.model_validate_json(row["search_request"])
                ) != normalized_search(search):
                    raise HTTPException(
                        409, "У подборки другие критерии. Создайте новый поиск."
                    )
                cid = row["id"]
            else:
                if not keys:
                    raise HTTPException(422, "Выберите хотя бы одну компанию")
                stamp = now()
                cid = conn.execute(
                    "INSERT INTO collections(search_key,source,title,search_request,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                    (
                        str(body.search_key),
                        body.source,
                        title_for(search.criteria),
                        search.model_dump_json(),
                        stamp,
                        stamp,
                    ),
                ).lastrowid
            # Companies with an answer or a sent request are retained even when deselected in search.
            existing = conn.execute(
                "SELECT * FROM collection_members WHERE collection_id=?", (cid,)
            ).fetchall()
            for old in existing:
                if (
                    old["supplier_key"] not in keys
                    and old["status"] == "selected"
                    and old["price_rub"] is None
                    and not old["terms"]
                    and not old["note"]
                ):
                    conn.execute(
                        "DELETE FROM collection_members WHERE id=?", (old["id"],)
                    )
            for key, data in snapshots.items():
                conn.execute(
                    "INSERT OR IGNORE INTO collection_members(collection_id,supplier_key,snapshot) VALUES(?,?,?)",
                    (cid, key, json.dumps(data, ensure_ascii=False)),
                )
            conn.execute("UPDATE collections SET updated_at=? WHERE id=?", (now(), cid))
        return get_collection(conn, cid)

    @router.patch("/{collection_id}/items/{item_id}")
    def patch(
        collection_id: int, item_id: int, body: MemberPatch, conn=Depends(get_conn)
    ):
        if not conn.execute(
            "SELECT 1 FROM collection_members WHERE id=? AND collection_id=?",
            (item_id, collection_id),
        ).fetchone():
            raise HTTPException(404, "Компания в подборке не найдена")
        fields = body.model_dump(exclude_unset=True)
        with conn:
            if fields:
                sets = ", ".join(f"{key}=?" for key in fields)
                conn.execute(
                    f"UPDATE collection_members SET {sets} WHERE id=? AND collection_id=?",
                    (*fields.values(), item_id, collection_id),
                )
                conn.execute(
                    "UPDATE collections SET updated_at=? WHERE id=?",
                    (now(), collection_id),
                )
        return get_collection(conn, collection_id)

    @router.post("/{collection_id}/items/{item_id}/draft", response_model=KPDraft)
    def draft(collection_id: int, item_id: int, conn=Depends(get_conn)):
        collection = get_collection(conn, collection_id)
        item = next((i for i in collection["items"] if i["id"] == item_id), None)
        if item is None:
            raise HTTPException(404, "Компания в подборке не найдена")
        profile = db.get_profile(conn)
        key = item["supplier"]["key"]
        if collection["source"] == "real":
            supplier = next(
                (s for s in load_real_case().suppliers if s.id == key), None
            )
            if supplier is None:
                raise HTTPException(404, "Компания не найдена")
            return make_draft(supplier, profile)
        suppliers = db.load_suppliers(conn, [int(key)])
        return RuleBasedKPWriter().draft(
            profile,
            SearchRequest.model_validate(collection["search"]).criteria,
            suppliers,
        )

    @router.post("/{collection_id}/compare")
    def compare(collection_id: int, body: CollectionCompare, conn=Depends(get_conn)):
        collection = get_collection(conn, collection_id)
        ids = list(dict.fromkeys(body.item_ids))
        if len(ids) < 2:
            raise HTTPException(422, "Выберите минимум две компании")
        by_id = {i["id"]: i for i in collection["items"]}
        if any(i not in by_id for i in ids):
            raise HTTPException(404, "Компания в подборке не найдена")
        items = [by_id[i] for i in ids]
        if body.kind == "offers":
            if not collection["quote_comparable"]:
                raise HTTPException(
                    422, "Для сравнения цен нужен конкретный товар, а не вся категория"
                )
            if any(i["status"] != "received" or i["price_rub"] is None for i in items):
                raise HTTPException(
                    422, "Внесите минимум два ответа с ценой за одну единицу товара"
                )
            best = min(i["price_rub"] for i in items)
            conclusion = "Сравниваются только цены и условия, внесённые из ответов. Стоимость доставки, НДС и состав товара проверьте перед выбором."
        else:
            best = None
            conclusion = "Сохранённые данные каталога на момент выбора компаний. Это не персональные коммерческие предложения."
        return dict(
            kind=body.kind,
            title=collection["title"],
            unit=collection["unit"],
            items=items,
            best_price=best,
            conclusion=conclusion,
            source=collection["source"],
        )

    return router
