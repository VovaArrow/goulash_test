from app.catalog import CATEGORIES
from app.db import get_profile, load_suppliers
from app.seed import generate_suppliers

from .conftest import TODAY


def test_volume_and_spread(conn):
    sups = load_suppliers(conn)
    assert 80 <= len(sups) <= 110
    assert {s.city for s in sups} == {"ekb", "chelyabinsk", "tyumen", "perm", "kazan", "moscow", "almaty", "astana"}
    assert set(CATEGORIES) <= {c for s in sups for c in s.categories}
    assert sum(s.city == "ekb" for s in sups) >= 25


def test_demo_scenario_suppliers(conn):
    names = {s.name: s for s in load_suppliers(conn)}
    assert names["Уральская сыроварня"].type == "producer"
    assert names["ТД «Молочный путь»"].city == "chelyabinsk"
    assert names["ТД «Молочный путь»"].min_order_kg == 300
    assert names["ООО «СырПром»"].linked_problem_company
    mozz_ekb = [s for s in names.values() if any(p.key == "mozzarella" for p in s.products) and s.city == "ekb"]
    assert len(mozz_ekb) >= 3


def test_risky_and_kz(conn):
    sups = load_suppliers(conn)
    assert sum(1 for s in sups if s.tax_debt_rub > 0 or s.bankruptcy or s.linked_problem_company) >= 5
    assert any(d.kind == "declaration" and d.valid_until and d.valid_until < TODAY for s in sups for d in s.documents)
    kz = [s for s in sups if s.country == "KZ"]
    assert kz and all(s.currency == "KZT" for s in kz)
    assert all(p.price is None or abs(p.price_rub - p.price / 6.0) < 0.01 for s in kz for p in s.products)
    assert any(p.price is None for s in sups for p in s.products)


def test_seed_ux_cases(conn):
    sups = load_suppliers(conn)
    assert sum(s.preferred_channel is None for s in sups) >= 2
    assert any(d.valid_until and 0 <= (d.valid_until - TODAY).days <= 30 for s in sups for d in s.documents)
    assert sum(s.deferral_days is None or s.delivery_schedule is None for s in sups) >= 10
    assert all(s.data_as_of <= TODAY for s in sups)


def test_load_by_ids_and_profile(conn):
    one = load_suppliers(conn, [1])
    assert len(one) == 1 and one[0].id == 1
    assert get_profile(conn).company == "Сушкоф и пицца"


def test_deterministic():
    assert generate_suppliers(42) == generate_suppliers(42)
