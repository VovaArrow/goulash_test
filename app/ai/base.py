"""Протоколы «умного» слоя.

Сейчас у каждого одна реализация на правилах (`app/ai/rules.py`).
LLM-реализация добавляется отдельным классом с теми же методами —
остальной код сервиса от этого не меняется.
"""
from typing import Protocol

from ..models import CompareResult, Criteria, KPDraft, Profile, ScoredSupplier, Supplier


class QueryParser(Protocol):
    def parse(self, text: str, default_city: str | None = None) -> Criteria: ...


class Explainer(Protocol):
    def reasons(self, item: ScoredSupplier, c: Criteria, median_price_rub: float | None) -> list[str]: ...

    def compare(self, items: list[ScoredSupplier], c: Criteria) -> CompareResult: ...


class KPWriter(Protocol):
    def draft(self, profile: Profile, c: Criteria, suppliers: list[Supplier]) -> KPDraft: ...
