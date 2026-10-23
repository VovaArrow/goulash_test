from uuid import uuid4
import pytest
from .test_api import client


def save(client, ids=("1", "2"), key=None, criteria=None, source="demo"):
    return client.post(
        "/api/collections",
        json={
            "search_key": key or str(uuid4()),
            "source": source,
            "search": {
                "criteria": criteria
                or {"product": "mozzarella", "city": "ekb", "volume_kg_month": 200}
            },
            "supplier_ids": list(ids),
        },
    )


def test_legacy_import_is_idempotent_and_preserves_answers(client):
    before = client.get("/api/kp").json()
    collections = [c for c in client.get("/api/collections").json() if any(i["migrated"] for i in c["items"])]
    assert len(collections) == 1 and len(collections[0]["items"]) == 3
    answer = next(i for i in collections[0]["items"] if i["status"] == "received")
    assert answer["price_rub"] == 485 and "Образец" in answer["note"]
    again = [c for c in client.get("/api/collections").json() if c["id"] == collections[0]["id"]]
    assert len(again[0]["items"]) == 3
    assert client.get("/api/kp").json() == before


def test_first_selection_creates_collection_and_next_selection_appends(client):
    key = str(uuid4())
    a = save(client, ("1",), key).json()
    b = save(client, ("1", "2"), key).json()
    assert a["id"] == b["id"] and len(b["items"]) == 2
    assert b["search"]["criteria"]["volume_kg_month"] == 200
    assert all(i["status"] == "selected" and i["price_rub"] is None for i in b["items"])


def test_two_searches_with_same_product_remain_separate(client):
    a = save(client).json()
    b = save(client).json()
    assert a["id"] != b["id"]


def test_criteria_cannot_be_overwritten_for_existing_search(client):
    key = str(uuid4())
    save(client, key=key)
    assert (
        save(
            client, key=key, criteria={"product": "mozzarella", "volume_kg_month": 500}
        ).status_code
        == 409
    )


def test_rapid_deselection_can_leave_empty_collection(client):
    key = str(uuid4())
    a = save(client, ("1",), key).json()
    b = save(client, (), key).json()
    assert b["id"] == a["id"] and b["items"] == []


def test_sent_or_answered_companies_are_retained_when_search_deselects(client):
    key = str(uuid4())
    c = save(client, ("1",), key).json()
    i = c["items"][0]
    client.patch(
        f"/api/collections/{c['id']}/items/{i['id']}",
        json={"status": "received", "price_rub": 450, "terms": "НДС включён"},
    )
    result = save(client, (), key).json()
    assert len(result["items"]) == 1 and result["items"][0]["price_rub"] == 450


def test_invalid_member_rolls_back_whole_save(client):
    before = len(client.get("/api/collections").json())
    assert save(client, ("1", "99999")).status_code == 404
    assert len(client.get("/api/collections").json()) == before


def test_catalog_snapshot_is_not_overwritten_by_reply(client):
    c = save(client).json()
    i = c["items"][0]
    facts = i["supplier"]["facts"]
    r = client.patch(
        f"/api/collections/{c['id']}/items/{i['id']}",
        json={
            "status": "received",
            "price_rub": 300,
            "terms": "Доставка 1000 ₽",
            "note": "Образец",
        },
    )
    item = r.json()["items"][0]
    assert item["supplier"]["facts"] == facts
    assert item["price_rub"] == 300 and item["terms"] == "Доставка 1000 ₽"


def test_comparison_never_substitutes_catalog_price_for_reply(client):
    c = save(client).json()
    ids = [i["id"] for i in c["items"]]
    url = f"/api/collections/{c['id']}/compare"
    assert (
        client.post(url, json={"kind": "suppliers", "item_ids": ids}).status_code == 200
    )
    assert client.post(url, json={"kind": "offers", "item_ids": ids}).status_code == 422
    for item, price in zip(c["items"], [900, 300]):
        client.patch(
            f"/api/collections/{c['id']}/items/{item['id']}",
            json={
                "status": "received",
                "price_rub": price,
                "terms": f"Условия {price}",
            },
        )
    result = client.post(url, json={"kind": "offers", "item_ids": ids}).json()
    assert result["best_price"] == 300 and [
        i["price_rub"] for i in result["items"]
    ] == [900, 300]


def test_comparison_rejects_items_from_other_collection(client):
    a = save(client).json()
    b = save(client).json()
    assert (
        client.post(
            f"/api/collections/{a['id']}/compare",
            json={
                "kind": "suppliers",
                "item_ids": [a["items"][0]["id"], b["items"][0]["id"]],
            },
        ).status_code
        == 404
    )


def test_real_suppliers_saved_with_sources_and_real_draft(client):
    c = save(client, ("prodservice", "mozaretti"), source="real").json()
    assert c["source"] == "real" and c["quote_comparable"]
    assert (
        c["items"][0]["supplier"]["sources"] and c["items"][0]["status"] == "selected"
    )
    i = c["items"][0]
    facts = {f["key"]: f for f in i["supplier"]["facts"]}
    assert facts["inn"]["value"] == "6670170834"
    sources = {s["id"] for s in i["supplier"]["sources"]}
    assert set(facts["inn"]["source_ids"]) <= sources
    r = client.post(f"/api/collections/{c['id']}/items/{i['id']}/draft")
    assert r.status_code == 200 and "Profi Cheese" in r.json()["body"]
    assert (
        next(x for x in client.get("/api/collections").json() if x["id"] == c["id"])[
            "items"
        ][0]["status"]
        == "selected"
    )


@pytest.mark.parametrize(
    "fields",
    [{"status": None}, {"status": "waiting"}, {"price_rub": -1}, {"terms": None}],
)
def test_invalid_updates_rejected(client, fields):
    c = save(client).json()
    i = c["items"][0]
    assert (
        client.patch(
            f"/api/collections/{c['id']}/items/{i['id']}", json=fields
        ).status_code
        == 422
    )


def test_demo_reset_clears_saved_collections(client):
    c = save(client, criteria={"product": "mozzarella", "city": "ekb", "raw": "unique"}).json()
    client.post("/api/demo/reset")
    collections = client.get("/api/collections").json()
    assert len(collections) == 4
    assert any(i["migrated"] for c in collections for i in c["items"])
    assert not any(x["search"]["criteria"]["raw"] == "unique" for x in collections)


def test_seeded_collections_have_comparable_answers_and_preserve_edits(client):
    from app import db
    from app.seed import seed_collections

    samples = [c for c in client.get("/api/collections").json() if not any(i["migrated"] for i in c["items"])]
    assert {c["search"]["criteria"]["product"] for c in samples} == {"chicken_fillet", "rice_sushi", "roll_container"}
    for collection in samples:
        assert len(collection["items"]) == 4
        assert len({i["supplier"]["key"] for i in collection["items"]}) == 4
        assert sorted(i["status"] for i in collection["items"]) == ["received", "received", "selected", "sent"]
        answers = [i for i in collection["items"] if i["status"] == "received"]
        assert all("вымышлены" in i["note"] for i in answers)
        response = client.post(f"/api/collections/{collection['id']}/compare", json={"kind": "offers", "item_ids": [i["id"] for i in answers]})
        assert response.status_code == 200
        assert [i["price_rub"] for i in response.json()["items"]] == [i["price_rub"] for i in answers]

    sample = samples[0]
    item = sample["items"][0]
    client.patch(f"/api/collections/{sample['id']}/items/{item['id']}", json={"price_rub": 123.45, "note": "Уточнено пользователем"})
    before = client.get("/api/collections").json()
    conn = db.connect(client.app.state.store.path)
    try:
        seed_collections(conn)
    finally:
        conn.close()
    assert client.get("/api/collections").json() == before


def test_legacy_repeated_requests_do_not_lose_prices(client):
    created = client.post(
        "/api/kp",
        json={
            "product_label": "Моцарелла",
            "criteria": {
                "product": "mozzarella",
                "city": "ekb",
                "volume_kg_month": 200,
            },
            "items": [{"supplier_id": 1, "channel": "copy"}],
        },
    ).json()[0]
    client.patch(
        f"/api/kp/{created['id']}", json={"status": "received", "price_rub": 750}
    )
    all_items = [i for c in client.get("/api/collections").json() for i in c["items"] if i["migrated"]]
    assert len(all_items) == 4
    assert sorted(i["price_rub"] for i in all_items if i["price_rub"] is not None) == [
        485,
        750,
    ]


def test_public_session_database_gets_collections_migration(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app import db

    with TestClient(create_app(str(tmp_path / "test.db"), public_demo=True)) as visitor:
        visitor.get("/api/profile")
        token = visitor.cookies.get("goulash_session")
        conn = db.connect(str(tmp_path / "sessions" / f"{token}.db"))
        conn.execute("DROP TABLE collection_members")
        conn.execute("DROP TABLE collections")
        conn.commit()
        conn.close()
        assert visitor.get("/api/collections").status_code == 200


def test_supplier_aliases_do_not_duplicate_and_decimal_reply_is_preserved(client):
    c = save(client, ("1", "01", "2")).json()
    assert len(c["items"]) == 2
    for item, price in zip(c["items"], [470.25, 455.5]):
        client.patch(
            f"/api/collections/{c['id']}/items/{item['id']}",
            json={"status": "received", "price_rub": price},
        )
    result = client.post(
        f"/api/collections/{c['id']}/compare",
        json={"kind": "offers", "item_ids": [i["id"] for i in c["items"]]},
    ).json()
    assert [i["price_rub"] for i in result["items"]] == [470.25, 455.5]
