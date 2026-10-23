import re

from app.ai.rules import RuleBasedExplainer, RuleBasedKPWriter, RuleBasedQueryParser
from app.models import Criteria, Filters, SearchRequest
from app.search import run_search

from .conftest import DEMO_TEXT, PROFILE, TODAY, make_supplier

PARSER = RuleBasedQueryParser()
EXPLAINER = RuleBasedExplainer(today=TODAY)
WRITER = RuleBasedKPWriter()


def _demo_items(conn):
    c = PARSER.parse(DEMO_TEXT)
    r = run_search(conn, SearchRequest(criteria=c, filters=Filters(hide_critical=False)), PROFILE, TODAY, explainer=EXPLAINER)
    by_name = {x.supplier.name: x for x in r.results}
    return c, by_name


def test_reasons_short_and_specific(conn):
    c, items = _demo_items(conn)
    for item in items.values():
        assert 2 <= len(item.reasons) <= 4, item.reasons
        assert len(set(item.reasons)) == len(item.reasons)
        assert all(re.search(r"\d", line) for line in item.reasons[:2])
        flag_texts = {f.text for f in item.flags}
        assert not flag_texts & set(item.reasons)
    ural = items["Уральская сыроварня"].reasons
    assert any("485 ₽/кг" in line for line in ural)
    assert any("отсрочка 14 дн." in line for line in ural)


def test_reasons_price_on_request(conn):
    _, items = _demo_items(conn)
    assert any("цена по запросу" in line for line in items["ООО «СырПром»"].reasons)


def test_compare_best_and_conclusion(conn):
    c, items = _demo_items(conn)
    ural, put = items["Уральская сыроварня"], items["ТД «Молочный путь»"]
    res = EXPLAINER.compare([ural, put], c)
    assert res.best["price"] == put.supplier.id
    assert res.best["min_order"] == ural.supplier.id
    assert res.best["score"] == ural.supplier.id
    assert res.best["distance"] == ural.supplier.id
    assert "Уральская сыроварня" in res.conclusion and "300 кг" in res.conclusion


def test_compare_mentions_critical_risk(conn):
    c, items = _demo_items(conn)
    res = EXPLAINER.compare([items["Уральская сыроварня"], items["ООО «СырПром»"]], c)
    assert "критичн" in res.conclusion.lower() and "СырПром" in res.conclusion


def test_kp_draft_contains_essentials(conn):
    _, items = _demo_items(conn)
    d = WRITER.draft(PROFILE, PARSER.parse(DEMO_TEXT), [items["Уральская сыроварня"].supplier])
    body = d.body.lower()
    for s in ["сушкоф и пицца", "моцарелла", "200 кг", "отсрочк", "деклараци", "образ", PROFILE.address.lower(), "26 точек"]:
        assert s in body, s
    assert "Моцарелла" in d.subject
    assert "также уточните" not in body


def test_kp_draft_asks_missing_terms():
    d = WRITER.draft(PROFILE, Criteria(product="mozzarella", category="dairy"),
                     [make_supplier(deferral_days=None, delivery_schedule=None)])
    assert "Также уточните: условия отсрочки, график доставки." in d.body


def test_kp_draft_without_product_uses_category():
    d = WRITER.draft(PROFILE, Criteria(category="fish"), [make_supplier()])
    assert "Рыба и морепродукты" in d.subject


def test_kp_draft_without_anything():
    d = WRITER.draft(PROFILE, Criteria(), [make_supplier()])
    assert d.subject and "образ" in d.body


def test_reasons_no_cross_product_median(conn):
    c = PARSER.parse("привет", default_city="ekb")
    r = run_search(conn, SearchRequest(criteria=c), PROFILE, TODAY, explainer=EXPLAINER)
    counts = {}
    for x in r.results:
        if x.offer and x.offer.price_rub is not None:
            counts[x.offer.key] = counts.get(x.offer.key, 0) + 1
    for x in r.results:
        if x.offer and counts.get(x.offer.key, 0) < 2:
            assert not any("медиан" in line for line in x.reasons), x.reasons


def test_partial_deferral_not_claimed_as_met(conn):
    c = PARSER.parse("моцарелла Екб, желательно отсрочка 30 дней")
    r = run_search(conn, SearchRequest(criteria=c), PROFILE, TODAY, explainer=EXPLAINER)
    partial = [x for x in r.results if x.supplier.deferral_days and x.supplier.deferral_days < 30]
    assert partial
    for x in partial:
        assert not any(line.startswith("Выполняет") and "отсрочка" in line for line in x.reasons)
        assert any("вы просили 30" in line for line in x.reasons), x.reasons
