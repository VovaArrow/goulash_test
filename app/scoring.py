"""Балл поставщика 0–100, флажки рисков и отчёт о надёжности.

Балл = взвешенная сумма четырёх частей (каждая 0–100): соответствие запросу,
надёжность, цена, логистика. Каждую часть можно разложить и показать
пользователю — никакого «чёрного ящика».
"""
import math
from datetime import date

from .catalog import ANIMAL_CATEGORIES, PRODUCTS
from .models import (Criteria, FactGroup, Flag, Mode, Product, ReliabilityReport, ScoreParts,
                     Supplier)

WEIGHTS: dict[str, dict[str, int]] = {
    "balance": {"match": 30, "reliability": 30, "price": 20, "logistics": 20},
    "reliability": {"match": 25, "reliability": 45, "price": 10, "logistics": 20},
    "price": {"match": 25, "reliability": 20, "price": 40, "logistics": 15},
    "speed": {"match": 25, "reliability": 20, "price": 15, "logistics": 40},
}

CRITICAL_AGE_MONTHS = 6
EXPIRING_DAYS = 30
MAX_DISTANCE_KM = 600


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _clamp(v: float) -> int:
    return int(round(max(0, min(100, v))))


def _months(s: Supplier, today: date) -> float:
    return (today - s.founded).days / 30.44


def _fmt_month(d: date) -> str:
    return d.strftime("%m.%Y")


def _rub(v: float) -> str:
    return f"{v:,.0f}".replace(",", " ")


def _declaration(s: Supplier) -> date | None | bool:
    """Самая поздняя дата окончания декларации/сертификата; False — документов нет."""
    docs = [d for d in s.documents if d.kind in ("declaration", "certificate")]
    if not docs:
        return False
    dates = [d.valid_until for d in docs]
    return None if None in dates else max(dates)


def _has(s: Supplier, kind: str) -> bool:
    return any(d.kind == kind for d in s.documents)


def _is_chilled(s: Supplier, offer: Product | None) -> bool:
    if offer and offer.key in PRODUCTS:
        return PRODUCTS[offer.key].chilled
    return bool(set(s.categories) & {"dairy", "meat", "fish", "ready"})


def best_offer(s: Supplier, c: Criteria) -> Product | None:
    if c.product:
        items = [p for p in s.products if p.key == c.product]
    elif c.category:
        items = [p for p in s.products if p.category == c.category]
    else:
        items = list(s.products)
    if not items:
        return None
    priced = [p for p in items if p.price_rub is not None]
    return min(priced, key=lambda p: p.price_rub) if priced else items[0]


def satisfies(s: Supplier, cond: str, c: Criteria, today: date) -> float:
    """Насколько поставщик выполняет условие: 1 — да, 0.5 — частично, 0 — нет/нет данных."""
    if cond == "declarations":
        valid = _declaration(s)
        return 1.0 if valid is None or (valid and valid >= today) else 0.0
    if cond in ("mercury", "halal", "chestny_znak"):
        return 1.0 if _has(s, cond) else 0.0
    if cond == "haccp":
        return 1.0 if any(d.kind == "haccp" and (d.valid_until is None or d.valid_until >= today)
                          for d in s.documents) else 0.0
    if cond == "own_delivery":
        return 1.0 if s.own_delivery else 0.0
    if cond == "cold_chain":
        return 1.0 if s.cold_chain else 0.0
    if cond == "producer_only":
        return 1.0 if s.type == "producer" else 0.0
    if cond == "deferral":
        if not s.deferral_days:
            return 0.0
        return 1.0 if c.deferral_days is None or s.deferral_days >= c.deferral_days else 0.5
    return 0.0


# --- Факты о компании (общие для флажков и отчёта о надёжности) ---

def _company_facts(s: Supplier, today: date) -> dict[str, list[Flag]]:
    months = _months(s, today)
    years = int(months // 12)
    reg = []
    founded = s.founded.strftime("%d.%m.%Y")
    if months < CRITICAL_AGE_MONTHS:
        reg.append(Flag(level="bad", text=f"компании {int(months)} мес.",
                        hint=f"Регистрация {founded} (ЕГРЮЛ, демо). Молодое юрлицо — частый признак перерегистрации после проблем."))
    elif months < 24:
        reg.append(Flag(level="warn", text=f"компании {int(months)} мес.",
                        hint=f"Регистрация {founded} (ЕГРЮЛ, демо). Короткая история — меньше данных о надёжности."))
    else:
        reg.append(Flag(level="ok", text=f"на рынке {years} {'год' if years == 1 else 'года' if years < 5 else 'лет'}",
                        hint=f"Регистрация {founded} (ЕГРЮЛ, демо)."))
    if s.employees < 10:
        reg.append(Flag(level="warn", text=f"{s.employees} сотрудников — маленькая команда",
                        hint="Среднесписочная численность, открытые данные ФНС (демо)."))
    else:
        reg.append(Flag(level="ok", text=f"{s.employees} сотрудников",
                        hint="Среднесписочная численность, открытые данные ФНС (демо)."))

    taxes = []
    if s.tax_debt_rub > 0:
        taxes.append(Flag(level="bad", text=f"налоговая задолженность {_rub(s.tax_debt_rub)} ₽",
                          hint="Открытые данные ФНС, набор «Задолженность по налогам» (демо)."))
    else:
        taxes.append(Flag(level="ok", text="налоговой задолженности нет",
                          hint="Открытые данные ФНС, набор «Задолженность по налогам» (демо)."))
    if s.revenue_mln_rub is None:
        taxes.append(Flag(level="warn", text="нет данных о выручке", hint="Бухгалтерская отчётность не найдена (ГИР БО, демо)."))
    else:
        taxes.append(Flag(level="ok", text=f"выручка {s.revenue_mln_rub:.0f} млн ₽ за год",
                          hint="Бухгалтерская отчётность, ГИР БО (демо)."))

    courts = []
    if s.bankruptcy:
        courts.append(Flag(level="bad", text="процедура банкротства", hint="Сообщение в Федресурсе (демо)."))
    else:
        courts.append(Flag(level="ok", text="банкротства нет", hint="Федресурс (демо)."))
    if s.lawsuits >= 5:
        courts.append(Flag(level="warn", text=f"{s.lawsuits} судебных дел за год", hint="Картотека арбитражных дел (демо)."))
    else:
        courts.append(Flag(level="ok", text="судов нет" if s.lawsuits == 0 else f"{s.lawsuits} судебных дела — немного",
                           hint="Картотека арбитражных дел (демо)."))

    links = []
    if s.linked_problem_company:
        links.append(Flag(level="bad", text=f"связь с {s.linked_problem_company}",
                          hint="Совпадает руководитель или учредитель (ЕГРЮЛ, демо)."))
    else:
        links.append(Flag(level="ok", text="связей с проблемными компаниями нет",
                          hint="Проверка руководителей и учредителей по ЕГРЮЛ (демо)."))
    if s.mass_address:
        links.append(Flag(level="warn", text="массовый адрес регистрации", hint="Реестр массовых адресов ФНС (демо)."))
    else:
        links.append(Flag(level="ok", text="адрес регистрации не массовый", hint="Реестр массовых адресов ФНС (демо)."))

    docs = []
    valid = _declaration(s)
    if valid is False:
        docs.append(Flag(level="warn", text="нет данных о декларациях", hint="В реестре ФСА записи не найдены (демо) — запросите в КП."))
    elif valid is not None and valid < today:
        docs.append(Flag(level="bad", text=f"декларация истекла {_fmt_month(valid)}", hint="Реестр деклараций Росаккредитации (демо)."))
    elif valid is not None and (valid - today).days <= EXPIRING_DAYS:
        docs.append(Flag(level="warn", text=f"декларация истекает через {(valid - today).days} дн.",
                         hint=f"Действует до {valid.strftime('%d.%m.%Y')} (реестр ФСА, демо)."))
    else:
        until = f" до {_fmt_month(valid)}" if valid else ""
        docs.append(Flag(level="ok", text=f"декларации{until}", hint="Реестр деклараций Росаккредитации (демо)."))
    if set(s.categories) & ANIMAL_CATEGORIES:
        if _has(s, "mercury"):
            docs.append(Flag(level="ok", text="Меркурий (ВСД)", hint="Площадка зарегистрирована в ВетИС «Цербер» (демо)."))
        else:
            docs.append(Flag(level="warn", text="нет данных о «Меркурии»", hint="Площадка не найдена в ВетИС «Цербер» (демо)."))
    if _has(s, "haccp"):
        docs.append(Flag(level="ok", text="ХАССП", hint="Сертификат системы ХАССП (демо)."))
    else:
        docs.append(Flag(level="warn", text="ХАССП — нет данных", hint="Сертификат не найден — уточните в КП."))
    return {"Регистрация и возраст": reg, "Налоги и долги": taxes, "Суды и банкротство": courts,
            "Связи и адрес": links, "Документы на продукцию": docs}


def reliability_report(s: Supplier, today: date) -> ReliabilityReport:
    groups = []
    counts = {"bad": 0, "warn": 0, "ok": 0}
    for title, facts in _company_facts(s, today).items():
        level = "bad" if any(f.level == "bad" for f in facts) else "warn" if any(f.level == "warn" for f in facts) else "ok"
        groups.append(FactGroup(title=title, level=level, facts=facts))
        for f in facts:
            counts[f.level] += 1
    verdict = "low" if counts["bad"] else "medium" if counts["warn"] >= 2 else "high"
    return ReliabilityReport(verdict=verdict, counts=counts, groups=groups, as_of=s.data_as_of)


# Какие «хорошие» факты о компании показывать в шорт-листе (остальные — только в карточке).
_SHORTLIST_OK_PREFIXES = ("на рынке", "декларации", "Меркурий", "ХАССП")


def risk_flags(s: Supplier, c: Criteria, offer: Product | None, distance_km: float, today: date) -> list[Flag]:
    flags: list[Flag] = []
    for facts in _company_facts(s, today).values():
        for f in facts:
            if f.level != "ok" or f.text.startswith(_SHORTLIST_OK_PREFIXES):
                flags.append(f)
    if s.type == "producer":
        flags.append(Flag(level="ok", text="производитель",
                          hint="Основной ОКВЭД — производство; в декларациях заявитель — изготовитель (демо)."))
    else:
        flags.append(Flag(level="warn", text="дистрибьютор, не производитель",
                          hint="Основной ОКВЭД — оптовая торговля (46.3x). Цена может включать наценку."))
    if c.volume_kg_month is not None and s.min_order_kg > c.volume_kg_month:
        flags.append(Flag(level="warn", text=f"мин. партия {s.min_order_kg:.0f} кг больше вашей потребности ({c.volume_kg_month:.0f} кг/мес)",
                          hint="Придётся брать больше нужного: излишки, заморозка или списания."))
    if s.delivery_days > 2:
        flags.append(Flag(level="warn", text=f"доставка {s.delivery_days} дн.", hint="Срок от заказа до приёмки на кухне (сайт поставщика, демо)."))
    if s.own_delivery:
        flags.append(Flag(level="ok", text="своя доставка" + (" с холодом" if s.cold_chain else ""),
                          hint="Собственный транспорт поставщика" + (", рефрижератор." if s.cold_chain else ".")))
    elif _is_chilled(s, offer):
        flags.append(Flag(level="warn", text="нет своей доставки для охлаждёнки", hint="Самовывоз или транспортная компания — риск для холодовой цепи."))
    if s.deferral_days:
        flags.append(Flag(level="ok", text=f"отсрочка {s.deferral_days} дн.", hint="Условия оплаты с сайта или из прайса (демо)."))
    if s.prepay_required:
        flags.append(Flag(level="warn", text="работает по предоплате", hint="Условия оплаты с сайта поставщика (демо)."))
    if offer is not None and offer.price_rub is None:
        flags.append(Flag(level="warn", text="цена по запросу", hint="Публичного прайса нет — цену даст КП."))
    if s.preferred_channel is None:
        flags.append(Flag(level="warn", text="нет e-mail и мессенджеров", hint="Найден только телефон — КП придётся запрашивать звонком."))
    if s.rating is not None and s.rating >= 4.5:
        flags.append(Flag(level="ok", text=f"рейтинг {s.rating:.1f} ({s.reviews_count} отзывов)", hint="Отзывы клиентов в 2ГИС (демо)."))
    return flags


def is_critical(flags: list[Flag]) -> bool:
    return any(f.level == "bad" for f in flags)


# --- Части балла ---

def price_part(price_rub: float | None, median_rub: float | None) -> int:
    """≤80% медианы → 100, ≥130% → 0, линейно между; без цены или медианы — 50."""
    if price_rub is None or not median_rub:
        return 50
    ratio = price_rub / median_rub
    return _clamp((1.3 - ratio) / 0.5 * 100)


def _match_part(s: Supplier, c: Criteria, offer: Product | None, today: date) -> int:
    if c.product:
        score = 60 if offer and offer.key == c.product else 25
    elif c.category:
        score = 50 if offer else 25
    else:
        score = 50
    if c.volume_kg_month is None:
        score += 15
    elif s.min_order_kg <= c.volume_kg_month:
        score += 25
    if c.preferred:
        score += 15 * sum(satisfies(s, p, c, today) for p in c.preferred) / len(c.preferred)
    else:
        score += 15
    return _clamp(score)


def _reliability_part(s: Supplier, today: date) -> int:
    months = _months(s, today)
    score = 50
    score += 10 if s.type == "producer" else 0
    score += 15 if months >= 60 else 8 if months >= 24 else -15 if months < 12 else 0
    valid = _declaration(s)
    if valid is None or (valid and valid >= today):
        score += 10
    elif valid:
        score -= 30
    if set(s.categories) & ANIMAL_CATEGORIES and _has(s, "mercury"):
        score += 5
    score += 10 if _has(s, "haccp") else 0
    score += 5 if s.rating is not None and s.rating >= 4.5 else 0
    score -= 10 if s.lawsuits >= 5 else 0
    score -= 15 if s.mass_address else 0
    score -= 30 if s.tax_debt_rub > 0 else 0
    score -= 50 if s.bankruptcy else 0
    score -= 40 if s.linked_problem_company else 0
    # Критичный риск не компенсируется хорошими документами и отзывами.
    critical = (months < CRITICAL_AGE_MONTHS or s.tax_debt_rub > 0 or s.bankruptcy
                or s.linked_problem_company or (valid and valid < today))
    return min(_clamp(score), 40) if critical else _clamp(score)


def _logistics_part(s: Supplier, offer: Product | None, distance_km: float) -> int:
    distance_score = max(0.0, 100 - distance_km / MAX_DISTANCE_KM * 100)
    score = 0.7 * distance_score
    score += 15 if s.own_delivery else 0
    score += 15 if (s.cold_chain or not _is_chilled(s, offer)) else 0
    score -= 10 * (s.delivery_days - 1)
    return _clamp(score)


def score_supplier(s: Supplier, c: Criteria, offer: Product | None, distance_km: float,
                   median_price_rub: float | None, mode: Mode, today: date) -> tuple[int, ScoreParts]:
    parts = ScoreParts(
        match=_match_part(s, c, offer, today),
        reliability=_reliability_part(s, today),
        price=price_part(offer.price_rub if offer else None, median_price_rub),
        logistics=_logistics_part(s, offer, distance_km),
    )
    w = WEIGHTS[mode]
    total = round(sum(getattr(parts, k) * v for k, v in w.items()) / 100)
    return total, parts


def logistics_text(s: Supplier, distance_km: float) -> str:
    days = s.delivery_days
    term = "1 день" if days <= 1 else "1–2 дня" if days == 2 else f"{days} дн."
    text = f"{round(distance_km)} км · {term} · {'своя доставка' if s.own_delivery else 'самовывоз / ТК'}"
    return text + (" · холод" if s.cold_chain else "")
