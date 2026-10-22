from datetime import date

from app.db import load_suppliers
from app.models import Criteria, Document, Product
from app.scoring import (WEIGHTS, haversine_km, is_critical, logistics_text, price_part,
                         reliability_report, risk_flags, satisfies, score_supplier)

from .conftest import TODAY, C, make_supplier


def _offer(price):
    return Product(name="Моцарелла", key="mozzarella", category="dairy", price=price, price_rub=price, unit="кг")


def test_haversine_ekb_chelyabinsk():
    assert 190 < haversine_km(56.8389, 60.6057, 55.1644, 61.4368) < 200


def test_good_supplier_has_no_critical_flags():
    assert not is_critical(risk_flags(make_supplier(), C, _offer(500), 10, TODAY))


def test_critical_flags():
    expired = [Document(kind="declaration", number="1", valid_until=date(2026, 1, 1))]
    for over in [dict(founded=date(2026, 6, 1)), dict(tax_debt_rub=120000), dict(bankruptcy=True),
                 dict(linked_problem_company="ООО X"), dict(documents=expired)]:
        assert is_critical(risk_flags(make_supplier(**over), C, None, 10, TODAY)), over


def test_critical_texts_are_specific():
    texts = [f.text for f in risk_flags(make_supplier(founded=date(2026, 6, 15)), C, None, 10, TODAY) if f.level == "bad"]
    assert any("4 мес." in t for t in texts)
    expired = [Document(kind="declaration", number="1", valid_until=date(2026, 1, 31))]
    texts = [f.text for f in risk_flags(make_supplier(documents=expired), C, None, 10, TODAY) if f.level == "bad"]
    assert any("истекла 01.2026" in t for t in texts)


def test_warn_min_order_above_volume():
    fl = risk_flags(make_supplier(min_order_kg=300), Criteria(volume_kg_month=200), None, 10, TODAY)
    assert any(f.level == "warn" and "партия" in f.text and "300" in f.text for f in fl)
    assert not is_critical(fl)


def test_doc_expiring_warn():
    soon = [Document(kind="declaration", number="1", valid_until=date(2026, 11, 10))]
    fl = risk_flags(make_supplier(documents=soon), C, None, 10, TODAY)
    assert any(f.level == "warn" and "истекает через 19 дн." in f.text for f in fl)
    assert not is_critical(fl)


def test_no_price_neutral_and_flagged():
    s = make_supplier()
    _, parts = score_supplier(s, C, _offer(None), 10, 500, "balance", TODAY)
    assert parts.price == 50
    assert any("по запросу" in f.text for f in risk_flags(s, C, _offer(None), 10, TODAY))


def test_price_part_bounds_and_monotonic():
    assert price_part(400, 500) == 100
    assert price_part(650, 500) == 0
    assert price_part(400, 500) > price_part(500, 500) > price_part(600, 500)
    assert price_part(None, 500) == 50 and price_part(500, None) == 50


def test_closer_is_better_logistics():
    _, near = score_supplier(make_supplier(), C, _offer(500), 10, None, "balance", TODAY)
    _, far = score_supplier(make_supplier(), C, _offer(500), 400, None, "balance", TODAY)
    assert near.logistics > far.logistics


def test_risky_reliability_lower_and_mode_changes_total():
    good = make_supplier()
    risky = make_supplier(linked_problem_company="ООО X", type="distributor")
    _, gp = score_supplier(good, C, _offer(520), 10, 500, "balance", TODAY)
    _, rp = score_supplier(risky, C, _offer(420), 10, 500, "balance", TODAY)
    assert rp.reliability <= 40 < gp.reliability
    by_price, _ = score_supplier(risky, C, _offer(420), 10, 500, "price", TODAY)
    by_rel, _ = score_supplier(risky, C, _offer(420), 10, 500, "reliability", TODAY)
    assert by_price > by_rel


def test_total_is_weighted_sum():
    total, p = score_supplier(make_supplier(), C, _offer(480), 30, 500, "balance", TODAY)
    w = WEIGHTS["balance"]
    assert w == {"match": 30, "reliability": 30, "price": 20, "logistics": 20}
    assert total == round((p.match * 30 + p.reliability * 30 + p.price * 20 + p.logistics * 20) / 100)
    assert all(0 <= v <= 100 for v in p.model_dump().values())


def test_min_order_fit_raises_match():
    _, fit = score_supplier(make_supplier(min_order_kg=50), C, _offer(500), 10, None, "balance", TODAY)
    _, unfit = score_supplier(make_supplier(min_order_kg=300), C, _offer(500), 10, None, "balance", TODAY)
    assert fit.match > unfit.match


def test_satisfies_deferral_partial_and_unknown():
    c = Criteria(preferred=["deferral"], deferral_days=14)
    assert satisfies(make_supplier(deferral_days=14), "deferral", c, TODAY) == 1
    assert satisfies(make_supplier(deferral_days=7), "deferral", c, TODAY) == 0.5
    assert satisfies(make_supplier(deferral_days=None), "deferral", c, TODAY) == 0
    assert satisfies(make_supplier(deferral_days=0), "deferral", c, TODAY) == 0


def test_satisfies_declarations_requires_valid():
    expired = [Document(kind="declaration", number="1", valid_until=date(2026, 1, 1))]
    assert satisfies(make_supplier(), "declarations", C, TODAY) == 1
    assert satisfies(make_supplier(documents=expired), "declarations", C, TODAY) == 0


def test_all_flags_have_hints(conn):
    for s in load_suppliers(conn):
        for f in risk_flags(s, C, s.products[0], 50, TODAY):
            assert f.hint, (s.name, f.text)


def test_reliability_report_verdicts(conn):
    by_name = {s.name: s for s in load_suppliers(conn)}
    bad = reliability_report(by_name["ООО «СырПром»"], TODAY)
    assert bad.verdict == "low" and bad.counts["bad"] >= 1
    good = reliability_report(by_name["Уральская сыроварня"], TODAY)
    assert good.verdict == "high" and good.counts["bad"] == 0
    assert good.as_of == by_name["Уральская сыроварня"].data_as_of
    assert [g.title for g in good.groups] == [
        "Регистрация и возраст", "Налоги и долги", "Суды и банкротство", "Связи и адрес", "Документы на продукцию"]
    assert sum(good.counts.values()) == sum(len(g.facts) for g in good.groups)


def test_group_level_is_worst_fact():
    rep = reliability_report(make_supplier(tax_debt_rub=1000), TODAY)
    taxes = next(g for g in rep.groups if g.title == "Налоги и долги")
    assert taxes.level == "bad"


def test_logistics_text_forms():
    assert logistics_text(make_supplier(delivery_days=2, own_delivery=True, cold_chain=True), 38.4) == "38 км · 1–2 дня · своя доставка · холод"
    assert logistics_text(make_supplier(delivery_days=1, own_delivery=False, cold_chain=False), 5) == "5 км · 1 день · самовывоз / ТК"
    assert logistics_text(make_supplier(delivery_days=4, own_delivery=False, cold_chain=False), 1400) == "1400 км · 4 дн. · самовывоз / ТК"
