CREATE TABLE IF NOT EXISTS suppliers (
    id                     INTEGER PRIMARY KEY,
    name                   TEXT NOT NULL,
    legal_name             TEXT NOT NULL,
    inn                    TEXT NOT NULL,
    country                TEXT NOT NULL CHECK (country IN ('RU', 'KZ')),
    city                   TEXT NOT NULL,
    address                TEXT NOT NULL,
    lat                    REAL NOT NULL,
    lon                    REAL NOT NULL,
    type                   TEXT NOT NULL CHECK (type IN ('producer', 'distributor')),
    founded                TEXT NOT NULL,
    employees              INTEGER NOT NULL,
    revenue_mln_rub        REAL,
    phone                  TEXT,
    email                  TEXT,
    website                TEXT,
    whatsapp               TEXT,
    telegram               TEXT,
    preferred_channel      TEXT,
    min_order_kg           REAL NOT NULL,
    own_delivery           INTEGER NOT NULL,
    cold_chain             INTEGER NOT NULL,
    delivery_days          INTEGER NOT NULL,
    delivery_radius_km     INTEGER NOT NULL,
    delivery_schedule      TEXT,
    deferral_days          INTEGER,
    prepay_required        INTEGER NOT NULL,
    currency               TEXT NOT NULL,
    rating                 REAL,
    reviews_count          INTEGER NOT NULL,
    lawsuits               INTEGER NOT NULL,
    tax_debt_rub           REAL NOT NULL,
    bankruptcy             INTEGER NOT NULL,
    mass_address           INTEGER NOT NULL,
    linked_problem_company TEXT,
    data_as_of             TEXT NOT NULL,
    sources                TEXT NOT NULL,   -- JSON: поле -> источник
    categories             TEXT NOT NULL    -- JSON: список ключей категорий
);
CREATE INDEX IF NOT EXISTS idx_suppliers_city ON suppliers(city);

CREATE TABLE IF NOT EXISTS products (
    id          INTEGER PRIMARY KEY,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    key         TEXT NOT NULL,
    name        TEXT NOT NULL,
    category    TEXT NOT NULL,
    price       REAL,
    price_rub   REAL,
    unit        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_products_key ON products(key);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);

CREATE TABLE IF NOT EXISTS documents (
    id          INTEGER PRIMARY KEY,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    number      TEXT,
    valid_until TEXT
);

CREATE TABLE IF NOT EXISTS notes (
    id          INTEGER PRIMARY KEY,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    author      TEXT NOT NULL,
    text        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kp_requests (
    id            INTEGER PRIMARY KEY,
    supplier_id   INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    product_label TEXT NOT NULL,
    channel       TEXT NOT NULL,
    status        TEXT NOT NULL,
    price_rub     REAL,
    note          TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kp_events (
    id         INTEGER PRIMARY KEY,
    request_id INTEGER NOT NULL REFERENCES kp_requests(id) ON DELETE CASCADE,
    at         TEXT NOT NULL,
    status     TEXT NOT NULL,
    text       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    company      TEXT NOT NULL,
    city         TEXT NOT NULL,
    address      TEXT NOT NULL,
    lat          REAL NOT NULL,
    lon          REAL NOT NULL,
    contact_name TEXT NOT NULL,
    phone        TEXT NOT NULL,
    email        TEXT NOT NULL,
    points       INTEGER NOT NULL
);
