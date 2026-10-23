"""Проверки ошибок, воспроизведённых при ревью проекта."""
from datetime import date

import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.ai.rules import RuleBasedQueryParser
from app.models import Criteria, Document, SearchRequest
from app.scoring import reliability_report, satisfies, score_supplier
from app.search import run_search
from .conftest import C, PROFILE, TODAY, make_supplier
from .test_api import client


def test_parser_separates_required_and_preferred_without_comma():
    c = RuleBasedQueryParser().parse("моцарелла обязательно декларации и желательно отсрочка 14 дней")
    assert c.required == ["declarations"]
    assert c.preferred == ["deferral"]
    assert c.deferral_days == 14


def test_negation_does_not_become_required_condition():
    c = RuleBasedQueryParser().parse("моцарелла без отсрочки Екатеринбург")
    assert "deferral" not in c.required + c.preferred
    assert c.unparsed
    c = RuleBasedQueryParser().parse("моцарелла, без предоплаты")
    assert "no_prepay" in c.required
    assert satisfies(make_supplier(prepay_required=True), "no_prepay", c, TODAY) == 0


def test_unsupported_city_needs_clarification(conn):
    c = RuleBasedQueryParser().parse("моцарелла 200 кг Новосибирск", default_city="ekb")
    assert c.location_unresolved and c.city is None and c.unparsed
    assert not run_search(conn, SearchRequest(criteria=c), PROFILE, TODAY).results


@pytest.mark.parametrize("kind", ["haccp", "halal", "mercury", "chestny_znak"])
def test_expired_documents_never_pass_filter(kind):
    s = make_supplier(documents=[Document(kind=kind, number="1", valid_until=date(2020, 1, 1))])
    assert satisfies(s, kind, C, TODAY) == 0
    assert not any(f.level == "ok" and kind == "haccp" and f.text == "ХАССП"
                   for g in reliability_report(s, TODAY).groups for f in g.facts)


def test_expired_haccp_adds_no_reliability_bonus():
    good = make_supplier()
    expired = good.model_copy(update={"documents": good.documents + [Document(kind="haccp", number="1", valid_until=date(2020, 1, 1))]})
    assert score_supplier(good, C, good.products[0], 10, 500, "balance", TODAY)[1].reliability == score_supplier(expired, C, expired.products[0], 10, 500, "balance", TODAY)[1].reliability


@pytest.mark.parametrize("criteria", [{"product": "unknown"}, {"city": "paris"}, {"radius_km": -1}, {"volume_kg_month": -200}, {"max_price_rub": -1}, {"product": "mozzarella", "category": "fish"}])
def test_invalid_criteria_rejected(client, criteria):
    assert client.post("/api/search", json={"criteria": criteria}).status_code == 422
    assert client.post("/api/kp/draft", json={"criteria": criteria, "supplier_ids": [1]}).status_code == 422


def test_null_status_does_not_crash_or_modify_request(client):
    assert client.patch("/api/kp/1", json={"status": None}).status_code == 422
    assert client.get("/api/kp").json()[-1]["status"] == "received"


def new_quote(client, sid, price, criteria):
    r = client.post("/api/kp", json={"criteria": criteria, "product_label": "Моцарелла", "items": [{"supplier_id": sid, "channel": "copy"}]})
    assert r.status_code == 200, r.text
    created = r.json()[0]
    assert created["status"] == "prepared" and created["reply_due"] is None
    return client.patch(f'/api/kp/{created["id"]}', json={"status": "received", "price_rub": price}).json()["id"]


def test_compare_uses_quoted_prices_and_preserves_criteria(client):
    criteria = {"product": "mozzarella", "city": "ekb", "volume_kg_month": 200}
    ids = [new_quote(client, 1, 900, criteria), new_quote(client, 2, 300, criteria)]
    r = client.post("/api/kp/compare", json={"request_ids": ids})
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["quoted"] and result["criteria"]["volume_kg_month"] == 200
    assert [i["offer"]["price_rub"] for i in result["items"]] == [900, 300]
    assert result["best"]["price"] == 2


def test_compare_rejects_different_needs(client):
    ids = [new_quote(client, 1, 400, {"product": "mozzarella", "volume_kg_month": 200}),
           new_quote(client, 2, 300, {"product": "mozzarella", "volume_kg_month": 500})]
    assert client.post("/api/kp/compare", json={"request_ids": ids}).status_code == 422


def test_public_demo_isolates_profile_notes_and_reset(tmp_path):
    app = create_app(str(tmp_path / 'base.db'), public_demo=True)
    with TestClient(app) as first, TestClient(app) as second:
        profile = first.get('/api/profile').json()
        profile['company'] = 'Моя сеть'
        first.put('/api/profile', json=profile)
        assert first.get('/api/profile').json()['company'] == 'Моя сеть'
        assert second.get('/api/profile').json()['company'] != 'Моя сеть'
        second.post('/api/demo/reset')
        assert first.get('/api/profile').json()['company'] == 'Моя сеть'
        first.post('/api/suppliers/1/notes', json={'text': 'Личная заметка'})
        assert not any(n['text'] == 'Личная заметка' for n in second.get('/api/suppliers/1').json()['notes'])
        assert 'HttpOnly' in first.get('/api/profile').headers['set-cookie']


def test_cross_site_write_rejected(client):
    assert client.post('/api/demo/reset', headers={'Origin': 'https://other.example'}).status_code == 403


def test_public_origin_behind_http_proxy_preserves_origin_checks_and_secure_cookie(tmp_path, monkeypatch):
    monkeypatch.setenv('GOULASH_PUBLIC_ORIGIN', 'https://demo.example/')
    with TestClient(create_app(str(tmp_path / 'base.db'), public_demo=True)) as visitor:
        reply = visitor.post('/api/parse', headers={'Origin': 'https://demo.example'}, json={'text': 'рис'})
        assert reply.status_code == 200
        assert 'Secure' in reply.headers['set-cookie']
        assert visitor.post('/api/parse', headers={'Origin': 'https://other.example'}, json={'text': 'рис'}).status_code == 403
        assert visitor.post('/api/parse', headers={'Origin': 'https://demo.example', 'Sec-Fetch-Site': 'cross-site'}, json={'text': 'рис'}).status_code == 403


def test_public_origin_rejects_urls_with_paths(tmp_path, monkeypatch):
    monkeypatch.setenv('GOULASH_PUBLIC_ORIGIN', 'https://demo.example/path')
    with pytest.raises(ValueError, match='GOULASH_PUBLIC_ORIGIN'):
        create_app(str(tmp_path / 'base.db'), public_demo=True)


def test_render_external_url_sets_canonical_origin_and_secure_cookie(tmp_path, monkeypatch):
    monkeypatch.delenv('GOULASH_PUBLIC_ORIGIN', raising=False)
    monkeypatch.setenv('RENDER_EXTERNAL_URL', 'https://goulash.onrender.com')
    with TestClient(create_app(str(tmp_path / 'base.db'), public_demo=True)) as visitor:
        reply = visitor.post('/api/parse', headers={'Origin': 'https://goulash.onrender.com'}, json={'text': 'рис'})
        assert reply.status_code == 200
        assert 'Secure' in reply.headers['set-cookie']
        assert visitor.post('/api/parse', headers={'Origin': 'https://other.example'}, json={'text': 'рис'}).status_code == 403


def test_compression_and_asset_cache_do_not_cache_private_responses(client):
    response = client.get('/api/real-case', headers={'Accept-Encoding': 'gzip'})
    assert response.headers['content-encoding'] == 'gzip'
    assert response.headers['cache-control'] == 'no-store'
    assert len(response.json()['suppliers']) == 5
    for path in ['/', '/api/profile', '/api/collections']:
        assert client.get(path).headers['cache-control'] == 'no-store'
    vendor = client.get('/static/vendor/alpine.min.js')
    assert vendor.headers['cache-control'] == 'public, max-age=86400'
    assert client.get('/static/vendor/alpine.min.js', headers={'If-None-Match': vendor.headers['etag']}).status_code == 304
    assert 'must-revalidate' in client.get('/static/app.js').headers['cache-control']
