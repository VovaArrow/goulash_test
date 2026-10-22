"""Поиск: кандидаты → жёсткие фильтры (с причинами скрытия) → балл → сортировка."""
import sqlite3
import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from . import db
from .catalog import CITIES, CONDITIONS
from .models import (Criteria, Filters, Flag, HiddenReason, Product, Profile, RelaxHint,
                     ScoredSupplier, SearchRequest, SearchResponse, Supplier)
from .scoring import (best_offer, haversine_km, is_critical, logistics_text, risk_flags,
                      satisfies, score_supplier)


class SupplierSource(Protocol):
    """Откуда берутся кандидаты. Сейчас — мок-база SQLite; в проде — 2ГИС + DaData + ФСА и т. д."""

    def candidates(self, c: Criteria) -> list[Supplier]: ...


class SqliteMockSource:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def candidates(self, c: Criteria) -> list[Supplier]:
        return db.load_suppliers(self.conn, db.candidate_ids(self.conn, c.product, c.category))


@dataclass
class _Candidate:
    supplier: Supplier
    offer: Product | None
    distance_km: float
    flags: list[Flag]


def origin_for(c: Criteria, profile: Profile) -> tuple[float, float, str]:
    """Точка доставки: кухня из профиля, если ищем в своём городе, иначе центр искомого города."""
    if not c.city or c.city == profile.city or c.city not in CITIES:
        return profile.lat, profile.lon, profile.address
    city = CITIES[c.city]
    return city.lat, city.lon, f"{city.name} (центр)"


def _hidden_reason(x: _Candidate, c: Criteria, f: Filters, today: date, skip: str | None = None) -> str | None:
    s = x.supplier
    if f.max_distance_km is not None:
        if x.distance_km > f.max_distance_km:
            return f"Дальше {f.max_distance_km} км"
    elif x.distance_km > c.radius_km and x.distance_km > s.delivery_radius_km:
        return f"Далеко: дальше {c.radius_km} км и не возят"
    for cond in c.required:
        if cond != skip and satisfies(s, cond, c, today) == 0:
            return f"Нет: {CONDITIONS[cond].label.lower()}"
    if c.max_price_rub is not None and x.offer and x.offer.price_rub is not None and x.offer.price_rub > c.max_price_rub:
        return f"Дороже {c.max_price_rub:g} ₽"
    if f.fit_min_order and c.volume_kg_month is not None and s.min_order_kg > c.volume_kg_month:
        return "Мин. партия больше потребности"
    if f.supplier_type != "any" and s.type != f.supplier_type:
        return "Не тот тип поставщика"
    if f.hide_critical and is_critical(x.flags):
        return "Критичные риски (⛔)"
    return None


def _apply_filters(cands: list[_Candidate], c: Criteria, f: Filters, today: date,
                   skip: str | None = None) -> tuple[list[_Candidate], Counter]:
    passed, hidden = [], Counter()
    for x in cands:
        reason = _hidden_reason(x, c, f, today, skip)
        if reason:
            hidden[reason] += 1
        else:
            passed.append(x)
    return passed, hidden


def _sort_key(sort: str):
    if sort == "price":
        return lambda r: (r.offer is None or r.offer.price_rub is None, r.offer.price_rub if r.offer and r.offer.price_rub else 0, -r.score)
    if sort == "distance":
        return lambda r: (r.distance_km, -r.score)
    return lambda r: (-r.score, r.distance_km)


def run_search(conn: sqlite3.Connection, req: SearchRequest, profile: Profile, today: date,
               explainer=None, source: SupplierSource | None = None) -> SearchResponse:
    c, f = req.criteria, req.filters
    lat, lon, label = origin_for(c, profile)
    source = source or SqliteMockSource(conn)
    cands = []
    for s in source.candidates(c):
        offer = best_offer(s, c)
        distance = round(haversine_km(lat, lon, s.lat, s.lon), 1)
        cands.append(_Candidate(s, offer, distance, risk_flags(s, c, offer, distance, today)))

    passed, hidden = _apply_filters(cands, c, f, today)
    prices = [x.offer.price_rub for x in passed if x.offer and x.offer.price_rub is not None]
    median = statistics.median(prices) if prices else None

    results = []
    for x in passed:
        total, parts = score_supplier(x.supplier, c, x.offer, x.distance_km, median, req.mode, today)
        item = ScoredSupplier(supplier=x.supplier, score=total, parts=parts, distance_km=x.distance_km,
                              offer=x.offer, flags=x.flags,
                              logistics_text=logistics_text(x.supplier, x.distance_km))
        if explainer is not None:
            item.reasons = explainer.reasons(item, c, median)
        results.append(item)
    results.sort(key=_sort_key(req.sort))

    facets = {cond: sum(1 for r in results if satisfies(r.supplier, cond, c, today) > 0) for cond in CONDITIONS}
    facets["fit_min_order"] = sum(1 for r in results if c.volume_kg_month is None or r.supplier.min_order_kg <= c.volume_kg_month)
    facets["producer"] = sum(1 for r in results if r.supplier.type == "producer")

    relax = []
    for cond in c.required:
        extra = len(_apply_filters(cands, c, f, today, skip=cond)[0]) - len(passed)
        if extra > 0:
            relax.append(RelaxHint(condition=cond, label=CONDITIONS[cond].label, extra=extra))

    return SearchResponse(
        criteria=c, results=results,
        hidden=[HiddenReason(reason=k, count=v) for k, v in hidden.most_common()],
        median_price_rub=median, origin={"lat": lat, "lon": lon, "label": label},
        facets=facets, relax=relax,
    )
