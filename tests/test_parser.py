import pytest

from app.ai.rules import RuleBasedQueryParser

from .conftest import DEMO_TEXT

P = RuleBasedQueryParser()


def test_full_example():
    c = P.parse(DEMO_TEXT)
    assert (c.product, c.category, c.city, c.volume_kg_month) == ("mozzarella", "dairy", "ekb", 200)
    assert c.required == ["declarations"]
    assert c.preferred == ["deferral"]
    assert c.deferral_days == 14
    assert c.raw == DEMO_TEXT


def test_weekly_volume_tons_and_kz():
    c = P.parse("лосось охлаждённый Алматы 0,5 т в неделю, меркурий")
    assert (c.product, c.city) == ("salmon", "almaty")
    assert c.volume_kg_month == pytest.approx(2165, rel=0.01)
    assert c.required == ["mercury"]


def test_packaging_price_cap_and_case_forms():
    c = P.parse("контейнеры для роллов в Казани до 12 руб")
    assert (c.product, c.category, c.city, c.max_price_rub) == ("roll_container", "packaging", "kazan", 12)


def test_price_cap_in_tenge_converted_to_rub():
    c = P.parse("рис для суши Астана до 900 тг")
    assert c.city == "astana" and c.max_price_rub == pytest.approx(150)


def test_category_only():
    c = P.parse("ищу поставщика рыбы и морепродуктов, Екб")
    assert c.product is None and c.category == "fish" and c.city == "ekb"


def test_nothing_understood_uses_default_city():
    c = P.parse("привет", default_city="ekb")
    assert c.category is None and c.product is None
    assert c.city == "ekb"
    assert c.unparsed == ["привет"]


def test_empty_text():
    c = P.parse("   ", default_city="ekb")
    assert c.city == "ekb" and c.unparsed == [] and c.category is None


def test_producer_only_and_halal():
    c = P.parse("куриное филе халяль только производители Тюмень")
    assert c.product == "chicken_fillet"
    assert set(c.required) == {"halal", "producer_only"}


def test_suggestions_only_unsaid_conditions():
    c = P.parse("моцарелла Екб 200 кг")
    assert c.required == [] and c.preferred == []
    assert "mercury" in c.suggestions and "cold_chain" in c.suggestions
    demo = P.parse(DEMO_TEXT)
    assert "declarations" not in demo.suggestions
    assert "mercury" in demo.suggestions


def test_no_suggestions_for_dry_goods():
    c = P.parse("коробки для пиццы Екб")
    assert c.product == "pizza_box" and c.suggestions == []


def test_lemma_forms_of_city_and_product():
    c = P.parse("Нужна моцарелла в Екатеринбурге")
    assert c.city == "ekb" and c.product == "mozzarella"
