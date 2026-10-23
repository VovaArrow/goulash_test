"""Реализации «умного» слоя на правилах — без внешних API."""
import re

from datetime import date

from ..catalog import (ANIMAL_CATEGORIES, CATEGORIES, CATEGORY_STEMS, CITIES, CONDITIONS,
                       KZT_PER_RUB, PRODUCTS)
from ..models import CompareResult, Criteria, KPDraft, Product, Profile, ScoredSupplier, Supplier
from ..scoring import satisfies

# Окончания для основ вида «рис$»: совпадает «рис», «риса», но не «риск».
_ENDINGS = r"(?:а|у|ом|ы|и|ов|е)?\b"
_WORD_START = r"(?<![а-яa-z0-9])"
_PREFERRED_MARKERS = re.compile(r"желательн|по возможности|хорошо бы|плюсом|было бы")
_FILLER = re.compile(
    r"\b(?:нужн\w*|ищу|ищем|поставщик\w*|для|в|во|на|и|мне|нам|пожалуйста|срочно|хочу|"
    r"купить|закуп\w*|обязательно|примерно|около)\b|[~≈.!?\-–—:()]"
)
_VOLUME = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(кг|килограмм\w*|тонн\w*|т)\b"
    r"(?:\s*(?:в|/|за)\s*(месяц|мес|неделю|нед|день|сутки))?"
)
_PRICE = re.compile(r"до\s*(\d+(?:[.,]\d+)?)\s*(₽|руб\w*|р\b|тг|тенге|₸)")
_RADIUS = re.compile(r"(?:до|радиус\w*)\s*(\d+)\s*км")
_DAYS = re.compile(r"(\d+)\s*(?:дн\w*|день|сут\w*)")
_SEGMENTS = re.compile(r",(?!\d)|;|\n|(?=\b(?:обязательно|желательно|по возможности|хорошо бы|плюсом|было бы)\b)")
_UNSUPPORTED_CITIES = re.compile(r"новосибир\w*|краснояр\w*|санкт[- ]петербург\w*|петербург\w*|спб|омск\w*|томск\w*|уф[аеуы]|самар\w*|ростов\w*|краснодар\w*|воронеж\w*|нижн\w*\s+новгород\w*")
_NEGATION = re.compile(r"(?:\bбез|\bне\s+нуж\w*|\bне\s+треб\w*)\s+(?:\w+\s+){0,2}$")


def _pattern(stem: str) -> re.Pattern:
    if stem.endswith("$"):
        body = re.escape(stem[:-1]) + _ENDINGS
    else:
        body = r"\w*\s+".join(re.escape(part) for part in stem.split())
    return re.compile(_WORD_START + body)


def _compile(stems_by_key: dict[str, tuple[str, ...]]) -> dict[str, list[tuple[re.Pattern, int]]]:
    return {key: [(_pattern(s), len(s)) for s in stems] for key, stems in stems_by_key.items()}


_PRODUCT_PATTERNS = _compile({k: p.stems for k, p in PRODUCTS.items()})
_CATEGORY_PATTERNS = _compile(CATEGORY_STEMS)
_CITY_PATTERNS = _compile({k: c.stems for k, c in CITIES.items()})
_CONDITION_PATTERNS = _compile({k: c.stems for k, c in CONDITIONS.items()})


def _normalize(text: str) -> str:
    return text.lower().replace("ё", "е")


def _best_match(text: str, patterns: dict[str, list[tuple[re.Pattern, int]]]) -> str | None:
    """Ключ с самой длинной совпавшей основой (длиннее — специфичнее)."""
    best, best_len = None, 0
    for key, pats in patterns.items():
        for pat, length in pats:
            if length > best_len and pat.search(text):
                best, best_len = key, length
    return best


def _matches(text: str, patterns: dict[str, list[tuple[re.Pattern, int]]]) -> list[str]:
    return [key for key, pats in patterns.items() if any(p.search(text) for p, _ in pats)]


def _num(s: str) -> float:
    return float(s.replace(",", "."))


class RuleBasedQueryParser:
    """Разбирает фразу закупщика на критерии поиска словарями и регулярными выражениями."""

    def parse(self, text: str, default_city: str | None = None) -> Criteria:
        norm = _normalize(text)
        product = _best_match(norm, _PRODUCT_PATTERNS)
        category = PRODUCTS[product].category if product else _best_match(norm, _CATEGORY_PATTERNS)
        city = _best_match(norm, _CITY_PATTERNS) or default_city
        unknown_city = _UNSUPPORTED_CITIES.search(norm)
        location_unresolved = bool(unknown_city)
        if location_unresolved:
            city = None

        volume = None
        if m := _VOLUME.search(norm):
            amount = _num(m.group(1)) * (1000 if m.group(2).startswith("т") else 1)
            period = m.group(3) or "месяц"
            factor = 4.33 if period.startswith("нед") else 30 if period in ("день", "сутки") else 1
            volume = round(amount * factor, 1)

        max_price = None
        if m := _PRICE.search(norm):
            max_price = _num(m.group(1))
            if m.group(2) in ("тг", "тенге", "₸"):
                max_price = round(max_price / KZT_PER_RUB, 2)

        radius = int(m.group(1)) if (m := _RADIUS.search(norm)) else None

        required: list[str] = []
        preferred: list[str] = []
        deferral_days = None
        unparsed: list[str] = [f"город «{unknown_city.group()}» пока не поддерживается, выберите город из списка"] if unknown_city else []
        for raw_segment in _SEGMENTS.split(text):
            segment = _normalize(raw_segment)
            if not segment.strip():
                continue
            conditions = []
            for cond, patterns in _CONDITION_PATTERNS.items():
                matches = [m for pat, _ in patterns for m in pat.finditer(segment)]
                if not matches:
                    continue
                if cond != "no_prepay" and all(_NEGATION.search(segment[:m.start()]) for m in matches):
                    unparsed.append(f"отрицание «{raw_segment.strip()}»: условие не применено")
                    continue
                conditions.append(cond)
            target = preferred if _PREFERRED_MARKERS.search(segment) else required
            for cond in conditions:
                if cond not in required and cond not in preferred:
                    target.append(cond)
            if "deferral" in conditions and (m := _DAYS.search(segment)):
                deferral_days = int(m.group(1))
            understood = (conditions or _best_match(segment, _PRODUCT_PATTERNS)
                          or _best_match(segment, _CATEGORY_PATTERNS) or _best_match(segment, _CITY_PATTERNS)
                          or _VOLUME.search(segment) or _PRICE.search(segment) or _RADIUS.search(segment))
            if not understood and _FILLER.sub(" ", segment).strip():
                unparsed.append(raw_segment.strip())

        mentioned = set(required) | set(preferred)
        suggestions = []
        if category in ANIMAL_CATEGORIES and "mercury" not in mentioned:
            suggestions.append("mercury")
        chilled = PRODUCTS[product].chilled if product else category in {"dairy", "meat", "fish", "ready"}
        if chilled and "cold_chain" not in mentioned:
            suggestions.append("cold_chain")

        criteria = Criteria(
            raw=text, category=category, product=product, city=city,
            volume_kg_month=volume, max_price_rub=max_price, required=required,
            preferred=preferred, deferral_days=deferral_days, unparsed=unparsed,
            suggestions=suggestions, location_unresolved=location_unresolved,
        )
        if radius is not None:
            criteria.radius_km = radius
        return criteria


# --- Объяснения и сравнение ---

def _money(v: float) -> str:
    return f"{v:,.0f}".replace(",", " ")


def price_text(offer: Product, currency: str) -> str:
    if offer.price is None:
        return "цена по запросу"
    if currency == "KZT":
        return f"{_money(offer.price)} ₸/{offer.unit} (≈{_money(offer.price_rub)} ₽)"
    return f"{_money(offer.price)} ₽/{offer.unit}"


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 19:
        return many
    return one if n % 10 == 1 else few if 2 <= n % 10 <= 4 else many


def _condition_phrase(s: Supplier, cond: str) -> str:
    if cond == "deferral" and s.deferral_days:
        return f"отсрочка {s.deferral_days} дн."
    return CONDITIONS[cond].label.lower()


class RuleBasedExplainer:
    """Короткие объяснения «почему он» и вывод сравнения — шаблонами по данным балла."""

    def __init__(self, today: date | None = None):
        self.today = today or date.today()

    def reasons(self, item: ScoredSupplier, c: Criteria, median_price_rub: float | None) -> list[str]:
        s, offer = item.supplier, item.offer
        lines = []
        if offer is not None:
            line = f"{offer.name} — {price_text(offer, s.currency)}"
            if offer.price_rub is not None and median_price_rub:
                diff = round((offer.price_rub / median_price_rub - 1) * 100)
                line += (f", на {abs(diff)}% {'ниже' if diff < 0 else 'выше'} медианы" if diff
                         else ", на уровне медианы") + f" ({_money(median_price_rub)} ₽)"
            elif median_price_rub:
                line += f" (медиана у других — {_money(median_price_rub)} ₽)"
            lines.append(line)
        else:
            n = len(s.products)
            lines.append(f"Ассортимент: {n} {_plural(n, 'позиция', 'позиции', 'позиций')} — "
                         + ", ".join(CATEGORIES[k].lower() for k in s.categories))
        term = "1 день" if s.delivery_days <= 1 else f"{s.delivery_days} дн."
        lines.append(f"{round(item.distance_km)} км до точки доставки, привезут за {term}")
        if c.volume_kg_month is not None and s.min_order_kg <= c.volume_kg_month:
            lines.append(f"Мин. партия {s.min_order_kg:.0f} кг — укладывается в ваши {c.volume_kg_month:.0f} кг/мес")
        conds = c.required + c.preferred
        met = [_condition_phrase(s, cond) for cond in conds if satisfies(s, cond, c, self.today) == 1]
        if met:
            lines.append("Выполняет ваши условия: " + ", ".join(met))
        if "deferral" in conds and satisfies(s, "deferral", c, self.today) == 0.5:
            lines.append(f"Отсрочка {s.deferral_days} дн. (вы просили {c.deferral_days})")
        return lines[:4]

    def compare(self, items: list[ScoredSupplier], c: Criteria) -> CompareResult:
        def pick(key, values, best=min):
            known = [(v, it.supplier.id) for it, v in zip(items, values) if v is not None]
            if known:
                target = best(v for v, _ in known)
                return next(i for v, i in known if v == target)
            return None

        best = {
            "score": pick("score", [it.score for it in items], max),
            "price": pick("price", [it.offer.price_rub if it.offer else None for it in items]),
            "min_order": pick("min_order", [it.supplier.min_order_kg for it in items]),
            "distance": pick("distance", [it.distance_km for it in items]),
            "delivery_days": pick("delivery_days", [it.supplier.delivery_days for it in items]),
            "deferral": pick("deferral", [it.supplier.deferral_days or 0 for it in items], max),
            "reliability": pick("reliability", [it.parts.reliability for it in items], max),
        }
        best = {k: v for k, v in best.items() if v is not None}
        by_id = {it.supplier.id: it for it in items}
        winner = by_id[best["score"]]
        vol = f"{c.volume_kg_month:.0f} кг/мес" if c.volume_kg_month else "вашей задачи"
        parts = [f"Для {vol} выгоднее всего {winner.supplier.name}: балл {winner.score}"
                 + (f", {price_text(winner.offer, winner.supplier.currency)}" if winner.offer else "")
                 + f", {winner.logistics_text}."]
        cheap = by_id.get(best.get("price"))
        if cheap and cheap is not winner and cheap.offer and winner.offer and winner.offer.price_rub:
            diff = round((1 - cheap.offer.price_rub / winner.offer.price_rub) * 100)
            buts = []
            if c.volume_kg_month and cheap.supplier.min_order_kg > c.volume_kg_month:
                buts.append(f"мин. партия {cheap.supplier.min_order_kg:.0f} кг при потребности "
                            f"{c.volume_kg_month:.0f} кг даст излишки")
            if cheap.distance_km > winner.distance_km + 50:
                buts.append(f"везти {round(cheap.distance_km)} км")
            if cheap.parts.reliability + 10 < winner.parts.reliability:
                buts.append("надёжность ниже")
            if diff > 0:
                parts.append(f"{cheap.supplier.name} дешевле на {diff}%" + (", но " + ", ".join(buts) if buts else "") + ".")
        for it in items:
            bad = [f.text for f in it.flags if f.level == "bad"]
            if bad:
                parts.append(f"У {it.supplier.name} критичные риски: {bad[0]} — сначала проверьте.")
        return CompareResult(items=items, best=best, conclusion=" ".join(parts))


# --- Письмо с запросом КП ---

_DOC_PHRASES = {
    "declarations": "декларации соответствия",
    "mercury": "оформление ВСД в «Меркурии»",
    "haccp": "сертификат ХАССП",
    "halal": "сертификат халяль",
    "chestny_znak": "маркировку «Честный знак»",
}
_TERM_PHRASES = {
    "cold_chain": "доставку с соблюдением холодовой цепи",
    "own_delivery": "собственную доставку до кухни",
}


class RuleBasedKPWriter:
    """Собирает письмо-запрос КП из критериев поиска и профиля компании."""

    def draft(self, profile: Profile, c: Criteria, suppliers: list[Supplier]) -> KPDraft:
        what = PRODUCTS[c.product].name if c.product else CATEGORIES.get(c.category or "", "продукты питания")
        unit = PRODUCTS[c.product].unit if c.product else "единицу"
        city = CITIES[profile.city].name if profile.city in CITIES else profile.city
        points = f"{profile.points} {_plural(profile.points, 'точка', 'точки', 'точек')}"
        conds = c.required + c.preferred
        lines = [
            "Добрый день!",
            "",
            f"Сеть «{profile.company}» ({city}, {points}) ищет поставщика по позиции «{what}».",
        ]
        if c.volume_kg_month:
            lines.append(f"Объём: около {c.volume_kg_month:.0f} кг в месяц.")
        lines += [f"Адрес доставки: {profile.address}.", "", "Просим прислать коммерческое предложение:",
                  f"— цену за {unit} при этом объёме;",
                  "— минимальную партию и график поставок;"]
        pay = "— условия доставки и оплаты"
        if "deferral" in conds:
            pay += f" (интересует отсрочка{f' {c.deferral_days} дней' if c.deferral_days else ''})"
        lines.append(pay + ";")
        docs = [_DOC_PHRASES[k] for k in conds if k in _DOC_PHRASES] or [_DOC_PHRASES["declarations"]]
        lines.append("— документы: " + ", ".join(docs) + ";")
        lines += [f"— {_TERM_PHRASES[k]};" for k in conds if k in _TERM_PHRASES]
        lines.append("— возможность передать образец для проработки.")
        missing = []
        if any(s.deferral_days is None for s in suppliers):
            missing.append("условия отсрочки")
        if any(s.delivery_schedule is None for s in suppliers):
            missing.append("график доставки")
        if missing:
            lines += ["", "Также уточните: " + ", ".join(missing) + "."]
        lines += ["", f"Контакт для ответа: {profile.contact_name}, {profile.phone}, {profile.email}.",
                  "", "С уважением,", profile.contact_name, f"«{profile.company}»"]
        return KPDraft(subject=f"Запрос КП: {what} — {profile.company}", body="\n".join(lines))
