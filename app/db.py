"""SQLite: подключение, схема и запросы. Чистый SQL без ORM."""
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from .models import Document, Product, Profile, Supplier

SCHEMA = Path(__file__).with_name("schema.sql")

_BOOL_FIELDS = ("own_delivery", "cold_chain", "prepay_required", "bankruptcy", "mass_address")


def connect(path: str) -> sqlite3.Connection:
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    conn.commit()


def is_empty(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0] == 0


def insert_supplier(conn: sqlite3.Connection, s: Supplier) -> None:
    row = s.model_dump(exclude={"products", "documents"})
    row["sources"] = json.dumps(row["sources"], ensure_ascii=False)
    row["categories"] = json.dumps(row["categories"], ensure_ascii=False)
    row["founded"] = s.founded.isoformat()
    row["data_as_of"] = s.data_as_of.isoformat()
    cols = ", ".join(row)
    marks = ", ".join(f":{k}" for k in row)
    conn.execute(f"INSERT INTO suppliers ({cols}) VALUES ({marks})", row)
    conn.executemany(
        "INSERT INTO products (supplier_id, key, name, category, price, price_rub, unit) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(s.id, p.key, p.name, p.category, p.price, p.price_rub, p.unit) for p in s.products],
    )
    conn.executemany(
        "INSERT INTO documents (supplier_id, kind, number, valid_until) VALUES (?, ?, ?, ?)",
        [(s.id, d.kind, d.number, d.valid_until.isoformat() if d.valid_until else None) for d in s.documents],
    )


def _ids_clause(ids: list[int] | None, column: str) -> tuple[str, list]:
    if ids is None:
        return "", []
    return f" WHERE {column} IN ({','.join('?' * len(ids))})", list(ids)


def load_suppliers(conn: sqlite3.Connection, ids: list[int] | None = None) -> list[Supplier]:
    if ids is not None and not ids:
        return []
    where, args = _ids_clause(ids, "id")
    rows = conn.execute(f"SELECT * FROM suppliers{where} ORDER BY id", args).fetchall()
    where_p, args_p = _ids_clause(ids, "supplier_id")
    products = defaultdict(list)
    for r in conn.execute(f"SELECT * FROM products{where_p} ORDER BY id", args_p):
        products[r["supplier_id"]].append(Product(
            name=r["name"], key=r["key"], category=r["category"],
            price=r["price"], price_rub=r["price_rub"], unit=r["unit"]))
    documents = defaultdict(list)
    for r in conn.execute(f"SELECT * FROM documents{where_p} ORDER BY id", args_p):
        documents[r["supplier_id"]].append(Document(kind=r["kind"], number=r["number"], valid_until=r["valid_until"]))
    result = []
    for r in rows:
        data = dict(r)
        data["sources"] = json.loads(data["sources"])
        data["categories"] = json.loads(data["categories"])
        for f in _BOOL_FIELDS:
            data[f] = bool(data[f])
        data["products"] = products[r["id"]]
        data["documents"] = documents[r["id"]]
        result.append(Supplier(**data))
    return result


def candidate_ids(conn: sqlite3.Connection, product: str | None, category: str | None) -> list[int] | None:
    """SQL-префильтр: поставщики с нужным товаром или категорией. None — без ограничения."""
    if product:
        rows = conn.execute("SELECT DISTINCT supplier_id FROM products WHERE key = ?", (product,))
    elif category:
        rows = conn.execute("SELECT DISTINCT supplier_id FROM products WHERE category = ?", (category,))
    else:
        return None
    return [r[0] for r in rows]


def get_profile(conn: sqlite3.Connection) -> Profile:
    row = conn.execute("SELECT * FROM profile WHERE id = 1").fetchone()
    data = dict(row)
    data.pop("id")
    return Profile(**data)


def save_profile(conn: sqlite3.Connection, p: Profile) -> None:
    data = p.model_dump()
    cols = ", ".join(data)
    marks = ", ".join(f":{k}" for k in data)
    conn.execute(f"INSERT OR REPLACE INTO profile (id, {cols}) VALUES (1, {marks})", data)
    conn.commit()
