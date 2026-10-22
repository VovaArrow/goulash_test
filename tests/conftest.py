from datetime import date

import pytest

from app.catalog import CITIES
from app.db import connect, get_profile, init_db
from app.models import Criteria, Document, Product, Supplier
from app.seed import DEFAULT_PROFILE, seed_db

TODAY = date(2026, 10, 22)
DEMO_TEXT = (
    "Нужна моцарелла для пиццы в Екатеринбург, ~200 кг в месяц, "
    "обязательно декларации, желательно отсрочка 14 дней"
)
C = Criteria(product="mozzarella", category="dairy", city="ekb", volume_kg_month=200)
PROFILE = DEFAULT_PROFILE


@pytest.fixture
def conn():
    c = connect(":memory:")
    init_db(c)
    seed_db(c)
    yield c
    c.close()


def make_supplier(**overrides) -> Supplier:
    """«Хороший» производитель в Екатеринбурге без рисков."""
    city = CITIES["ekb"]
    base = dict(
        id=1, name="Тестовая сыроварня", legal_name="ООО «Тест»", inn="6600000000",
        country="RU", city="ekb", address="Екатеринбург, ул. Тестовая, 1",
        lat=city.lat, lon=city.lon, type="producer", founded=date(2015, 3, 1),
        employees=40, revenue_mln_rub=120.0, phone="+7 343 000-00-00",
        email="opt@test.example", website=None, whatsapp="+79000000000", telegram=None,
        preferred_channel="whatsapp", min_order_kg=50, own_delivery=True, cold_chain=True,
        delivery_days=1, delivery_radius_km=150, delivery_schedule="пн–сб",
        deferral_days=7, prepay_required=False, currency="RUB", rating=4.7,
        reviews_count=20, lawsuits=0, tax_debt_rub=0, bankruptcy=False,
        mass_address=False, linked_problem_company=None, data_as_of=date(2026, 10, 20),
        sources={"contacts": "2ГИС (демо)"}, categories=["dairy"],
        products=[Product(name="Моцарелла", key="mozzarella", category="dairy",
                          price=500, price_rub=500, unit="кг")],
        documents=[Document(kind="declaration", number="ЕАЭС N RU Д-0001", valid_until=date(2027, 12, 31)),
                   Document(kind="mercury", number=None, valid_until=None)],
    )
    base.update(overrides)
    return Supplier(**base)
