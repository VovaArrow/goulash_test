"""Протоколы «умного» слоя.

Сейчас у каждого одна реализация на правилах (`app/ai/rules.py`).
LLM-реализация добавляется отдельным классом с теми же методами —
остальной код сервиса от этого не меняется.
"""
from typing import Protocol

from ..models import Criteria


class QueryParser(Protocol):
    def parse(self, text: str, default_city: str | None = None) -> Criteria: ...
