"""SQLite: подключение, схема и запросы. Чистый SQL без ORM."""
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from .models import Criteria, Document, KPEvent, KPRequest, Note, Product, Profile, Supplier

SCHEMA = Path(__file__).with_name("schema.sql")

_BOOL_FIELDS = ("own_delivery", "cold_chain", "prepay_required", "bankruptcy", "mass_address")


def connect(path: str) -> sqlite3.Connection:
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    columns = {r[1] for r in conn.execute("PRAGMA table_info(kp_requests)")}
    if "criteria" not in columns:
        conn.execute("ALTER TABLE kp_requests ADD COLUMN criteria TEXT")
    if "sent_at" not in columns:
        conn.execute("ALTER TABLE kp_requests ADD COLUMN sent_at TEXT")
        conn.execute("UPDATE kp_requests SET sent_at = created_at WHERE status != 'prepared'")
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


# --- Заметки команды ---

def add_note(conn: sqlite3.Connection, supplier_id: int, author: str, text: str, now: datetime) -> Note:
    cur = conn.execute("INSERT INTO notes (supplier_id, author, text, created_at) VALUES (?, ?, ?, ?)",
                       (supplier_id, author, text, now.isoformat(timespec="seconds")))
    conn.commit()
    return Note(id=cur.lastrowid, supplier_id=supplier_id, author=author, text=text, created_at=now)


def list_notes(conn: sqlite3.Connection, supplier_id: int) -> list[Note]:
    rows = conn.execute("SELECT * FROM notes WHERE supplier_id = ? ORDER BY created_at DESC, id DESC", (supplier_id,))
    return [Note(**dict(r)) for r in rows]


# --- Запросы КП ---

KP_REPLY_DAYS = 3
CHANNEL_LABELS = {"email": "e-mail", "whatsapp": "WhatsApp", "telegram": "Telegram", "copy": "скопированный текст"}


def add_kp_event(conn: sqlite3.Connection, request_id: int, status: str, text: str, now: datetime) -> None:
    conn.execute("INSERT INTO kp_events (request_id, at, status, text) VALUES (?, ?, ?, ?)",
                 (request_id, now.isoformat(timespec="seconds"), status, text))


def create_kp(conn: sqlite3.Connection, supplier_id: int, product_label: str, channel: str, now: datetime,
              status: str = "prepared", price_rub: float | None = None, note: str | None = None,
              criteria: Criteria | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO kp_requests (supplier_id, product_label, channel, status, price_rub, note, created_at, criteria, sent_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (supplier_id, product_label, channel, status, price_rub, note, now.isoformat(timespec="seconds"),
         criteria.model_dump_json() if criteria else None,
         now.isoformat(timespec="seconds") if status != "prepared" else None))
    add_kp_event(conn, cur.lastrowid, status,
                 "Запрос подготовлен, отправку подтвердите вручную" if status == "prepared"
                 else f"Отправлено через {CHANNEL_LABELS[channel]}", now)
    conn.commit()
    return cur.lastrowid


def _kp_from_row(conn: sqlite3.Connection, r: sqlite3.Row, now: datetime) -> KPRequest:
    created = datetime.fromisoformat(r["created_at"])
    sent_at = datetime.fromisoformat(r["sent_at"]) if r["sent_at"] else None
    days = max(0, (now.date() - sent_at.date()).days) if sent_at else 0
    status = r["status"]
    if status == "sent" and days >= 1:
        status = "waiting"
    events = [KPEvent(at=e["at"], status=e["status"], text=e["text"]) for e in
              conn.execute("SELECT * FROM kp_events WHERE request_id = ? ORDER BY at, id", (r["id"],))]
    return KPRequest(id=r["id"], supplier_id=r["supplier_id"], supplier_name=r["supplier_name"],
                     product_label=r["product_label"], channel=r["channel"], status=status,
                     price_rub=r["price_rub"], note=r["note"], created_at=created, days_waiting=days,
                     reply_due=(sent_at + timedelta(days=KP_REPLY_DAYS)).date() if sent_at else None, events=events,
                     sent_at=sent_at, criteria=Criteria.model_validate_json(r["criteria"]) if r["criteria"] else None)


_KP_SELECT = ("SELECT k.*, s.name AS supplier_name FROM kp_requests k "
              "JOIN suppliers s ON s.id = k.supplier_id")


def list_kp(conn: sqlite3.Connection, now: datetime) -> list[KPRequest]:
    rows = conn.execute(f"{_KP_SELECT} ORDER BY k.created_at DESC, k.id DESC").fetchall()
    return [_kp_from_row(conn, r, now) for r in rows]


def get_kp(conn: sqlite3.Connection, request_id: int, now: datetime) -> KPRequest | None:
    r = conn.execute(f"{_KP_SELECT} WHERE k.id = ?", (request_id,)).fetchone()
    return _kp_from_row(conn, r, now) if r else None


KP_STATUS_EVENTS = {"prepared": "Запрос подготовлен", "sent": "Отправлено повторно", "waiting": "Ждём ответ",
                    "received": "КП получено", "declined": "Поставщик отказал"}


def update_kp(conn: sqlite3.Connection, request_id: int, fields: dict, now: datetime) -> None:
    current = conn.execute("SELECT status, sent_at FROM kp_requests WHERE id = ?", (request_id,)).fetchone()
    if "status" in fields and fields["status"] != current["status"]:
        add_kp_event(conn, request_id, fields["status"], KP_STATUS_EVENTS[fields["status"]], now)
        if fields["status"] == "prepared":
            fields["sent_at"] = None
        elif fields["status"] == "sent" or not current["sent_at"]:
            fields["sent_at"] = now.isoformat(timespec="seconds")
    if fields:
        sets = ", ".join(f"{k} = :{k}" for k in fields)
        conn.execute(f"UPDATE kp_requests SET {sets} WHERE id = :id", {**fields, "id": request_id})
    conn.commit()
