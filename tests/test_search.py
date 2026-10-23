import statistics

from app.ai.rules import RuleBasedQueryParser
from app.models import Criteria, Filters, SearchRequest
from app.search import origin_for, run_search

from .conftest import DEMO_TEXT, PROFILE, TODAY

PARSER = RuleBasedQueryParser()


def _search(conn, criteria, **kw):
    return run_search(conn, SearchRequest(criteria=criteria, **kw), PROFILE, TODAY)


def test_demo_scenario_order(conn):
    r = _search(conn, PARSER.parse(DEMO_TEXT))
    names = [x.supplier.name for x in r.results]
    assert names[0] == "Уральская сыроварня"
    assert "ТД «Молочный путь»" in names
    assert "ООО «СырПром»" not in names
    assert any(h.reason.startswith("Критичные риски") and h.count >= 1 for h in r.hidden)
    first = r.results[0]
    assert first.logistics_text.startswith(f"{round(first.distance_km)} км · 1 день")


def test_show_critical_when_filter_off(conn):
    r = _search(conn, PARSER.parse(DEMO_TEXT), filters=Filters(hide_critical=False))
    syr = next(x for x in r.results if x.supplier.name == "ООО «СырПром»")
    assert any(f.level == "bad" for f in syr.flags)


def test_required_is_hard_filter(conn):
    c = Criteria(category="meat", city="ekb", radius_km=2000, required=["halal"])
    r = _search(conn, c)
    assert r.results and all(any(d.kind == "halal" for d in x.supplier.documents) for x in r.results)
    assert any(h.reason == "Нет: халяль" for h in r.hidden)


def test_everything_filtered_returns_reasons(conn):
    c = Criteria(product="mozzarella", category="dairy", city="ekb",
                 required=["halal", "haccp", "producer_only"], max_price_rub=1)
    r = _search(conn, c)
    assert r.results == []
    assert sum(h.count for h in r.hidden) > 0


def test_relax_hint_counts_extra_suppliers(conn):
    c = Criteria(product="mozzarella", category="dairy", city="ekb", required=["deferral", "halal"])
    r = _search(conn, c)
    halal = next(h for h in r.relax if h.condition == "halal")
    assert halal.extra > 0 and halal.label == "Халяль"
    assert all(h.extra > 0 for h in r.relax)


def test_facets_count_within_results(conn):
    r = _search(conn, PARSER.parse(DEMO_TEXT))
    assert r.facets["declarations"] == len(r.results)       # обязательное условие — у всех
    assert 0 <= r.facets["mercury"] <= len(r.results)
    assert r.facets["fit_min_order"] < len(r.results)        # «Молочный путь» с партией 300 кг


def test_other_city_origin(conn):
    r = _search(conn, Criteria(category="fish", city="almaty"))
    assert r.origin["label"] == "Алматы (центр)"
    assert r.results and r.results[0].supplier.city == "almaty"
    assert all(x.distance_km <= 300 or x.distance_km <= x.supplier.delivery_radius_km for x in r.results)


def test_origin_is_profile_kitchen_in_profile_city():
    lat, lon, label = origin_for(Criteria(city="ekb"), PROFILE)
    assert (lat, lon, label) == (PROFILE.lat, PROFILE.lon, PROFILE.address)


def test_strict_distance_filter(conn):
    r = _search(conn, Criteria(category="grocery", city="ekb"), filters=Filters(max_distance_km=50))
    assert r.results and all(x.distance_km <= 50 for x in r.results)


def test_no_price_not_hidden_by_price_cap_and_not_in_median(conn):
    c = Criteria(product="mozzarella", category="dairy", city="ekb", max_price_rub=470)
    r = _search(conn, c, filters=Filters(hide_critical=False))
    names = [x.supplier.name for x in r.results]
    assert "ООО «СырПром»" in names and "Уральская сыроварня" not in names
    assert any(h.reason == "Дороже 470 ₽" for h in r.hidden)
    priced = [x.offer.price_rub for x in r.results if x.offer and x.offer.price_rub is not None]
    assert r.median_price_rub == (statistics.median(priced) if len(priced) >= 2 else None)


def test_sort_by_price_and_distance(conn):
    c = Criteria(product="mozzarella", category="dairy", city="ekb")
    by_price = _search(conn, c, sort="price", filters=Filters(hide_critical=False)).results
    prices = [x.offer.price_rub for x in by_price]
    known = [p for p in prices if p is not None]
    assert known == sorted(known) and prices[len(known):] == [None] * (len(prices) - len(known))
    by_dist = _search(conn, c, sort="distance").results
    assert [x.distance_km for x in by_dist] == sorted(x.distance_km for x in by_dist)


def test_nothing_parsed_returns_city_suppliers(conn):
    r = _search(conn, PARSER.parse("привет", default_city="ekb"))
    assert len(r.results) >= 10
    assert all(x.distance_km <= 300 or x.distance_km <= x.supplier.delivery_radius_km for x in r.results)


def test_mode_changes_order(conn):
    c = Criteria(product="mozzarella", category="dairy", city="ekb")
    a = [x.supplier.id for x in _search(conn, c, mode="price").results]
    b = [x.supplier.id for x in _search(conn, c, mode="reliability").results]
    assert sorted(a) == sorted(b) and a != b


def test_median_only_within_same_product(conn):
    r = _search(conn, PARSER.parse("упаковка Екб", default_city="ekb"))
    assert r.median_price_rub is None                      # товар не задан — общей медианы нет
    by_key = {}
    for x in r.results:
        if x.offer and x.offer.price_rub is not None:
            by_key.setdefault(x.offer.key, []).append(x.offer.price_rub)
    for x in r.results:
        if x.offer and len(by_key.get(x.offer.key, [])) < 2:
            assert x.parts.price == 50, x.supplier.name
    assert r.medians and all(k in by_key for k in r.medians)


def test_required_deferral_needs_full_term(conn):
    c = PARSER.parse("моцарелла Екб, обязательно отсрочка 30 дней")
    r = _search(conn, c)
    assert all((x.supplier.deferral_days or 0) >= 30 for x in r.results)
