"""Реализации «умного» слоя на правилах — без внешних API."""
import re

from ..catalog import (ANIMAL_CATEGORIES, CATEGORY_STEMS, CITIES, CONDITIONS, KZT_PER_RUB,
                       PRODUCTS)
from ..models import Criteria

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
_SEGMENTS = re.compile(r",(?!\d)|;|\n")


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
        unparsed: list[str] = []
        for raw_segment in _SEGMENTS.split(text):
            segment = _normalize(raw_segment)
            if not segment.strip():
                continue
            conditions = _matches(segment, _CONDITION_PATTERNS)
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
            suggestions=suggestions,
        )
        if radius:
            criteria.radius_km = radius
        return criteria
