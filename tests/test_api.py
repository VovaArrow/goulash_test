from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

from .conftest import DEMO_TEXT


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(str(tmp_path / "test.db"))) as c:
        yield c


def _search(client, text=DEMO_TEXT, **extra):
    criteria = client.post("/api/parse", json={"text": text}).json()
    return client.post("/api/search", json={"criteria": criteria, **extra}).json()


def _id(client, name):
    for x in _search(client, filters={"hide_critical": False})["results"]:
        if x["supplier"]["name"] == name:
            return x["supplier"]["id"]
    raise AssertionError(name)


def test_meta(client):
    m = client.get("/api/meta").json()
    assert len(m["categories"]) == 10 and len(m["cities"]) == 8
    assert m["examples"][0] == DEMO_TEXT
    assert {c["key"] for c in m["conditions"]} >= {"declarations", "mercury", "deferral"}
    assert [x["key"] for x in m["modes"]] == ["balance", "reliability", "price", "speed"]


def test_parse_then_search_flow(client):
    r = _search(client)
    assert r["results"][0]["supplier"]["name"] == "Уральская сыроварня"
    assert r["results"][0]["reasons"]
    assert r["origin"]["label"].startswith("Екатеринбург, ул. Малышева")


def test_parse_uses_profile_city(client):
    assert client.post("/api/parse", json={"text": "привет"}).json()["city"] == "ekb"


def test_parse_rejects_too_long(client):
    assert client.post("/api/parse", json={"text": "а" * 1001}).status_code == 422


def test_supplier_detail_with_reliability_and_notes(client):
    sid = _id(client, "Уральская сыроварня")
    d = client.get(f"/api/suppliers/{sid}").json()
    assert d["supplier"]["name"] == "Уральская сыроварня"
    assert d["reliability"]["verdict"] == "high"
    assert any("Дегустация" in n["text"] for n in d["notes"])


def test_supplier_404(client):
    r = client.get("/api/suppliers/99999")
    assert r.status_code == 404 and r.json()["detail"] == "Поставщик не найден"


def test_note_stored_verbatim(client):
    sid = _id(client, "Уральская сыроварня")
    r = client.post(f"/api/suppliers/{sid}/notes", json={"author": "Тест", "text": "  <script>alert(1)</script> "})
    assert r.status_code == 200 and r.json()["text"] == "<script>alert(1)</script>"
    notes = client.get(f"/api/suppliers/{sid}").json()["notes"]
    assert notes[0]["text"] == "<script>alert(1)</script>"


def test_empty_note_422(client):
    sid = _id(client, "Уральская сыроварня")
    assert client.post(f"/api/suppliers/{sid}/notes", json={"author": "Тест", "text": "   "}).status_code == 422


def test_compare_requires_2_to_4(client):
    criteria = client.post("/api/parse", json={"text": DEMO_TEXT}).json()
    r = client.post("/api/compare", json={"criteria": criteria, "supplier_ids": [1]})
    assert r.status_code == 422 and "от 2 до 4" in r.text


def test_compare_ok(client):
    criteria = client.post("/api/parse", json={"text": DEMO_TEXT}).json()
    ids = [_id(client, "Уральская сыроварня"), _id(client, "ТД «Молочный путь»")]
    res = client.post("/api/compare", json={"criteria": criteria, "supplier_ids": ids}).json()
    assert [x["supplier"]["id"] for x in res["items"]] == ids
    assert res["best"]["price"] == ids[1] and "300 кг" in res["conclusion"]


def test_kp_lifecycle(client):
    criteria = client.post("/api/parse", json={"text": DEMO_TEXT}).json()
    ids = [_id(client, "Уральская сыроварня"), _id(client, "ТД «Молочный путь»")]
    draft = client.post("/api/kp/draft", json={"criteria": criteria, "supplier_ids": ids}).json()
    assert "Сушкоф и пицца" in draft["body"]
    before = len(client.get("/api/kp").json())
    created = client.post("/api/kp", json={"product_label": "Моцарелла",
                                           "items": [{"supplier_id": ids[0], "channel": "whatsapp"},
                                                     {"supplier_id": ids[1], "channel": "email"}]}).json()
    assert [x["status"] for x in created] == ["prepared", "prepared"]
    assert "подготовлен" in created[0]["events"][0]["text"]
    assert created[0]["reply_due"] is None
    sent = client.patch(f'/api/kp/{created[0]["id"]}', json={"status": "sent"}).json()
    sent_at = datetime.fromisoformat(sent["sent_at"])
    assert sent["reply_due"] == (sent_at + timedelta(days=3)).date().isoformat()
    rid = created[0]["id"]
    upd = client.patch(f"/api/kp/{rid}", json={"status": "received", "price_rub": 480, "note": "ок"}).json()
    assert upd["status"] == "received" and upd["price_rub"] == 480 and len(upd["events"]) == 3
    listed = client.get("/api/kp").json()
    assert len(listed) == before + 2 and listed[0]["id"] in {c["id"] for c in created}


def test_kp_seeded_and_waiting_derived(client):
    kp = client.get("/api/kp").json()
    assert len(kp) == 3
    assert {x["status"] for x in kp} == {"received", "waiting", "sent"}
    waiting = next(x for x in kp if x["status"] == "waiting")
    assert waiting["days_waiting"] == 2


def test_kp_channel_without_contacts_422(client):
    from app.db import connect, load_suppliers
    conn = connect(client.app.state.store.path)
    nocontact = [s.id for s in load_suppliers(conn) if s.preferred_channel is None]
    conn.close()
    assert nocontact
    r = client.post("/api/kp", json={"product_label": "x", "items": [{"supplier_id": nocontact[0], "channel": "email"}]})
    assert r.status_code == 422 and "нет контактов" in r.text
    ok = client.post("/api/kp", json={"product_label": "x", "items": [{"supplier_id": nocontact[0], "channel": "copy"}]})
    assert ok.status_code == 200


def test_kp_patch_404(client):
    assert client.patch("/api/kp/9999", json={"status": "received"}).status_code == 404


def test_profile_update_changes_origin(client):
    p = client.get("/api/profile").json()
    p.update(city="almaty", address="Алматы, пр. Абая, 10")
    saved = client.put("/api/profile", json=p).json()
    assert saved["city"] == "almaty" and abs(saved["lat"] - 43.2389) < 0.01
    assert client.post("/api/parse", json={"text": "салаты"}).json()["city"] == "almaty"


def test_profile_unknown_city_422(client):
    p = client.get("/api/profile").json()
    p["city"] = "paris"
    assert client.put("/api/profile", json=p).status_code == 422


def test_demo_reset_restores(client):
    sid = _id(client, "Уральская сыроварня")
    client.post(f"/api/suppliers/{sid}/notes", json={"author": "Тест", "text": "лишняя"})
    assert client.post("/api/demo/reset").json() == {"ok": True}
    texts = [n["text"] for n in client.get(f"/api/suppliers/{sid}").json()["notes"]]
    assert "лишняя" not in texts and any("Дегустация" in t for t in texts)
    assert len(client.get("/api/kp").json()) == 3


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "Goulash" in r.text


def test_parallel_requests_do_not_deadlock(client):
    import threading
    criteria = client.post("/api/parse", json={"text": DEMO_TEXT}).json()
    done = []

    def hit():
        r = client.post("/api/search", json={"criteria": criteria})
        done.append(r.status_code)

    threads = [threading.Thread(target=hit, daemon=True) for _ in range(80)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert len(done) == 80 and set(done) == {200}
