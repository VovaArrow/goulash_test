# Goulash · Поставщики — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Локальный веб-сервис, где руководитель закупок описывает потребность обычной фразой и получает объяснимый шорт-лист поставщиков продуктов (с фильтрами, картой, карточкой, сравнением) и отправляет им запрос КП.

**Architecture:** FastAPI отдаёт JSON API и статический SPA-фронт без сборки (Alpine.js + Leaflet). Данные — SQLite, заполняемая детерминированным сид-генератором мок-поставщиков. «AI»-слой (разбор запроса, объяснения, письмо КП) — на правилах, за протоколами, чтобы позже добавить LLM-реализацию.

**Tech Stack:** Python ≥3.11, FastAPI, Pydantic v2, sqlite3 (stdlib, чистый SQL), uvicorn, pytest + httpx (TestClient), uv; фронт — Alpine.js 3.14.x, Leaflet 1.9.4 (оба вендорятся в `static/vendor/`), шрифт Onest (Google Fonts, с fallback на system-ui).

**Spec:** `docs/superpowers/specs/2026-10-22-supplier-search-design.md`

## Global Constraints

- Весь интерфейс, сообщения API об ошибках, README — на русском.
- Никаких внешних API и ключей: LLM не подключаем; сеть нужна только для тайлов карты и шрифта (без сети — заглушка карты и system-ui).
- Цвета: фон `#1A1A1A`, карточки `#202020`, линии `#2E2E2E`, акцент `#21FFFF`, ok `#3DDC97`, warn `#FFC24B`, bad `#FF5C5C`; кнопки — белые «таблетки» с тёмным текстом.
- Веса балла (режим «Баланс»): соответствие 30, надёжность 30, цена 20, логистика 20.
- Мок-данные выдуманы; в шапке UI и README пометка «демо-данные».
- Путь к базе — переменная окружения `GOULASH_DB` (по умолчанию `data/goulash.db`); при отсутствии файла база создаётся и заполняется сидом при старте.
- Валюта: поставщики KZ хранят цену в ₸ и `price_rub` по курсу `KZT_PER_RUB = 6.0`; балл, медиана, фильтры — в ₽; UI показывает обе.
- Все функции, зависящие от «сегодня» (возраст компании, срок декларации), принимают `today: date`; в коде приложения передаётся `date.today()`, в тестах — `date(2026, 10, 22)`.
- Запуск: `make run` → http://localhost:8000.
- Вести `CHANGELOG.md` (правило Владимира): запись на каждую задачу.
- `CHANGELOG.md` уже существует — дописывать, не перезаписывать.
- Каждая задача включает свои пункты из раздела «UX-дополнения» ниже (тесты оттуда — часть шага Step 1 задачи).

## Review Focus

1. Запрос, из которого ничего не распознано («привет»), → не 500 и не пустой экран: чипов нет, показано «не понял: …» и подсказки-примеры, поиск по всей базе в городе профиля. (Task 2, Task 4 тесты)
2. Жёсткие фильтры отсекли всех → пустой список с причинами «скрыто N: …» и предложением ослабить условия. (Task 4 тест)
3. Поставщик без цены → не попадает в медиану, балл цены нейтральный 50, флажок «цена по запросу»; фильтр по цене его не скрывает. (Task 3, Task 4 тесты)
4. Поиск в другом городе, чем город профиля → расстояние считается от центра искомого города, а не от кухни в Екатеринбурге. (Task 4 тест)
5. Опасный текст в заметке/письме (`<script>`) → выводится как текст (Alpine `x-text`, не `x-html`). (Task 7/8: правило «только x-text для пользовательских данных»; Task 6 тест на сохранение «как есть»)

---

## Структура файлов

```
pyproject.toml, Makefile, .gitignore, CHANGELOG.md, README.md, Dockerfile, docker-compose.yml
app/
  main.py          — create_app(db_path) : FastAPI; статика, роутер, инициализация БД
  models.py        — Pydantic-модели (единые типы для всех модулей)
  db.py            — connect(), init_db(), schema.sql, функции-запросы
  schema.sql
  catalog.py       — справочники: категории, товары (синонимы, цены), города (координаты, падежи), условия
  seed.py          — generate_suppliers(seed=42) и seed_db(conn)
  scoring.py       — haversine_km, weights, score_supplier, risk_flags
  search.py        — run_search(conn, req, profile, today)
  ai/base.py       — Protocol: QueryParser, Explainer, KPWriter
  ai/rules.py      — RuleBasedQueryParser, RuleBasedExplainer, RuleBasedKPWriter
  api.py           — APIRouter /api/*
static/
  index.html, app.js, styles.css, logo.svg, vendor/{alpine.min.js, leaflet.js, leaflet.css, images/}
tests/
  conftest.py, test_seed.py, test_parser.py, test_scoring.py, test_search.py, test_texts.py, test_api.py
```

## Общие типы (`app/models.py`) — используются во всех задачах

```python
Condition = Literal["declarations","mercury","haccp","halal","chestny_znak",
                    "own_delivery","cold_chain","deferral","producer_only"]
Mode = Literal["balance","reliability","price","speed"]
SortKey = Literal["score","price","distance"]

class Criteria(BaseModel):
    raw: str = ""
    category: str | None = None        # ключ из catalog.CATEGORIES, напр. "dairy"
    product: str | None = None         # ключ товара, напр. "mozzarella"
    city: str | None = None            # ключ города, напр. "ekb"
    radius_km: int = 300
    volume_kg_month: float | None = None
    max_price_rub: float | None = None
    required: list[Condition] = []
    preferred: list[Condition] = []
    deferral_days: int | None = None
    unparsed: list[str] = []           # фрагменты, которые не поняли

class Filters(BaseModel):
    max_distance_km: int | None = None
    fit_min_order: bool = False        # мин. партия ≤ volume_kg_month
    hide_critical: bool = True
    supplier_type: Literal["any","producer","distributor"] = "any"

class SearchRequest(BaseModel):
    criteria: Criteria; filters: Filters = Filters(); mode: Mode = "balance"; sort: SortKey = "score"

class Product(BaseModel):  name: str; key: str; category: str; price: float | None; price_rub: float | None; unit: str
class Document(BaseModel): kind: Literal["declaration","certificate","mercury","haccp","chestny_znak","halal"]; number: str | None; valid_until: date | None
class Supplier(BaseModel):
    id: int; name: str; legal_name: str; inn: str; country: Literal["RU","KZ"]; city: str; address: str
    lat: float; lon: float; type: Literal["producer","distributor"]; founded: date; employees: int
    revenue_mln_rub: float | None; phone: str; email: str; website: str | None; whatsapp: str | None; telegram: str | None
    min_order_kg: float; own_delivery: bool; cold_chain: bool; delivery_days: int; delivery_radius_km: int
    delivery_schedule: str; deferral_days: int; prepay_required: bool; currency: Literal["RUB","KZT"]
    rating: float | None; reviews_count: int
    lawsuits: int; tax_debt_rub: float; bankruptcy: bool; mass_address: bool; linked_problem_company: str | None
    sources: dict[str, str]            # поле → источник («2ГИС (демо)», «ЕГРЮЛ (демо)», «сайт», «ФСА (демо)»)
    categories: list[str]; products: list[Product]; documents: list[Document]

class Flag(BaseModel): level: Literal["ok","warn","bad"]; text: str
class ScoreParts(BaseModel): match: int; reliability: int; price: int; logistics: int
class ScoredSupplier(BaseModel):
    supplier: Supplier; score: int; parts: ScoreParts; distance_km: float
    offer: Product | None              # лучший подходящий товар
    flags: list[Flag]; reasons: list[str]   # reasons — 2–4 строки «почему», от Explainer
class HiddenReason(BaseModel): reason: str; count: int
class SearchResponse(BaseModel):
    criteria: Criteria; results: list[ScoredSupplier]; hidden: list[HiddenReason]
    median_price_rub: float | None; origin: dict   # {"lat","lon","label"}
class Profile(BaseModel): company: str; city: str; address: str; lat: float; lon: float; contact_name: str; phone: str; email: str; points: int
class Note(BaseModel): id: int; supplier_id: int; author: str; text: str; created_at: datetime
KPStatus = Literal["sent","waiting","received","declined"]
class KPRequest(BaseModel): id: int; supplier_id: int; supplier_name: str; product_label: str; channel: Literal["email","whatsapp","telegram","copy"]; status: KPStatus; price_rub: float | None; note: str | None; created_at: datetime; days_waiting: int
class KPDraft(BaseModel): subject: str; body: str
class CompareResult(BaseModel): items: list[ScoredSupplier]; best: dict[str, int]  # строка → supplier.id; conclusion: str
```

---

### Task 1: Каркас, справочники, схема БД, сид мок-данных

**Files:** Create `pyproject.toml`, `Makefile`, `.gitignore`, `CHANGELOG.md`, `app/__init__.py`, `app/models.py`, `app/catalog.py`, `app/schema.sql`, `app/db.py`, `app/seed.py`, `tests/conftest.py`, `tests/test_seed.py`

**Interfaces:**
- Produces: все типы из «Общие типы»; `catalog.CATEGORIES: dict[str, str]` (key → русское название); `catalog.PRODUCTS: dict[str, ProductSpec]` (`ProductSpec`: key, name, category, stems: list[str], unit, price_rub: tuple[float,float], chilled: bool); `catalog.CITIES: dict[str, CitySpec]` (key, name, country, lat, lon, stems); `catalog.CONDITIONS: dict[Condition, ConditionSpec]` (label, stems); `db.connect(path) -> sqlite3.Connection` (row_factory=Row, foreign_keys on); `db.init_db(conn)`; `db.load_suppliers(conn, ids: list[int] | None = None) -> list[Supplier]`; `db.get_profile(conn) -> Profile`; `seed.generate_suppliers(seed: int = 42) -> list[Supplier]`; `seed.seed_db(conn)` (очищает и заполняет suppliers/products/documents/notes/kp_requests/profile).
- `tests/conftest.py`: фикстура `conn` — in-memory БД после `init_db` + `seed_db`; константы `TODAY = date(2026, 10, 22)`, `DEMO_TEXT` (первый пример из спеки), `C = Criteria(product="mozzarella", category="dairy", city="ekb", volume_kg_month=200)`, `PROFILE` (профиль по умолчанию); фабрика `make_supplier(**overrides) -> Supplier` («хороший» производитель в Екб: 2015 г., декларация до 2027-12-31, без рисков).

- [ ] **Step 1:** `git init`, `uv init --app` → `pyproject.toml` с зависимостями `fastapi`, `uvicorn[standard]`, `pydantic>=2`; dev: `pytest`, `httpx`. `.gitignore`: `.venv/ data/ __pycache__/ .superpowers/ .DS_Store`. `Makefile`: `install` (`uv sync`), `run` (`uv run uvicorn app.main:app --reload --port 8000`), `test` (`uv run pytest -q`), `seed` (пересоздать `data/goulash.db`).
- [ ] **Step 2: Написать `tests/test_seed.py`**

```python
def test_volume_and_spread(conn):
    sups = load_suppliers(conn)
    assert 80 <= len(sups) <= 110
    assert {s.city for s in sups} == {"ekb","chelyabinsk","tyumen","perm","kazan","moscow","almaty","astana"}
    assert set(CATEGORIES) <= {c for s in sups for c in s.categories}   # все 10 категорий есть
    assert sum(s.city == "ekb" for s in sups) >= 25

def test_demo_scenario_suppliers(conn):
    names = {s.name: s for s in load_suppliers(conn)}
    assert names["Уральская сыроварня"].type == "producer"
    assert names["ТД «Молочный путь»"].city == "chelyabinsk" and names["ТД «Молочный путь»"].min_order_kg == 300
    assert names["ООО «СырПром»"].linked_problem_company           # «плохой» герой
    mozz_ekb = [s for s in names.values() if any(p.key=="mozzarella" for p in s.products) and s.city=="ekb"]
    assert len(mozz_ekb) >= 3

def test_risky_and_kz(conn):
    sups = load_suppliers(conn)
    assert sum(1 for s in sups if s.tax_debt_rub > 0 or s.bankruptcy or s.linked_problem_company) >= 5
    assert any(d.kind=="declaration" and d.valid_until and d.valid_until < TODAY for s in sups for d in s.documents)
    kz = [s for s in sups if s.country=="KZ"]
    assert kz and all(s.currency=="KZT" for s in kz)
    assert all(p.price is None or abs(p.price_rub - p.price/6.0) < 0.01 for s in kz for p in s.products)
    assert any(p.price is None for s in sups for p in s.products)          # есть «цена по запросу»

def test_deterministic():
    assert generate_suppliers(42) == generate_suppliers(42)
```

- [ ] **Step 3:** Запустить `uv run pytest tests/test_seed.py -v` → FAIL (нет модулей).
- [ ] **Step 4: Реализовать** `models.py` (типы выше), `catalog.py`, `schema.sql` + `db.py`, `seed.py`.
  - Категории (10): `dairy` Молочная продукция и сыры, `meat` Мясо и птица, `fish` Рыба и морепродукты, `veg` Овощи, фрукты, зелень, `grocery` Бакалея, `sauces` Соусы и специи, `ready` Готовая еда и полуфабрикаты, `bakery` Хлеб и выпечка, `drinks` Напитки, `packaging` Упаковка и одноразовая посуда.
  - Товары: 4–6 на категорию с основами слов и ценой в ₽ (например, `mozzarella` «Моцарелла» stems `["моцарел"]` 420–560 ₽/кг; `salmon` «Лосось охлаждённый» stems `["лосос","сёмг","семг"]` 1500–2100; `rice_sushi` «Рис для суши» `["рис"]`; `nori`; `unagi_sauce` `["унаги"]`; `pizza_box` «Коробка для пиццы» `["коробк","пицц"]` шт 18–35; `roll_container` «Контейнер для роллов» `["контейнер","бокс"]` шт 6–14; `veg_cut` «Овощная нарезка» `["нарезк"]`; `chicken_fillet` `["курин","куриц"]`; `ready_salads` «Готовые салаты» `["салат"]` и т. п.). `chilled=True` для dairy, meat, fish, ready, veg_cut.
  - Города: ekb Екатеринбург (56.8389, 60.6057, stems `["екатеринбург","екб"]`), chelyabinsk Челябинск (55.1644, 61.4368), tyumen Тюмень (57.1530, 65.5343), perm Пермь (58.0105, 56.2502), kazan Казань (55.7961, 49.1064), moscow Москва (55.7558, 37.6173, `["москв","мск"]`), almaty Алматы (43.2389, 76.8897), astana Астана (51.1694, 71.4491).
  - Условия: declarations «декларации/сертификаты» `["деклар","сертифик"]`; mercury «Меркурий (ВСД)» `["меркур","всд","ветсерт"]`; haccp «ХАССП» `["хассп","haccp"]`; halal «халяль» `["халял"]`; chestny_znak «Честный знак» `["честн","маркиров"]`; own_delivery «своя доставка» `["доставк"]`; cold_chain «холодовая цепь» `["холод","рефриж"]`; deferral «отсрочка» `["отсроч"]`; producer_only «только производители» `["производител"]`.
  - Сид: `random.Random(seed)`; герои демо (Уральская сыроварня — Екб, 12 км от профиля, моцарелла 480–520 → price 495, мин. партия 50, отсрочка 7, декларация до 2027-03-31, Меркурий; ТД «Молочный путь» — Челябинск, дистрибьютор, 455, мин. 300, отсрочка 14; ООО «СырПром» — Екб, основано 2026-06-15, цена None, `linked_problem_company="ООО «СырЛайн» (ликвидировано, санкции Роспотребнадзора 2025)"`) + сгенерированные: название из шаблонов («Фабрика-кухня …», «ТД …», «… Продукт»), координаты = центр города ± ~0.08°, ИНН/БИН 10/12 цифр (выдуманные), 1–3 категории, 2–5 товаров с ценой из диапазона (10% без цены), документы с датами 2025-01..2028-12, ~8% «плохих» по разным причинам, `sources` для contacts/legal/documents/prices.
  - Профиль по умолчанию: «Сушкоф и пицца», ekb, «Екатеринбург, ул. Малышева, 51 (кухня)», 56.8380, 60.6150, контакт «Владимир, отдел закупок», `points=26`.
  - Таблицы: `suppliers` (все скалярные поля Supplier, `sources` и `categories` — JSON-текст), `products`, `documents`, `notes`, `kp_requests`, `profile` (одна строка id=1). Индексы: `products(key)`, `products(category)`, `suppliers(city)`.
- [ ] **Step 5:** `uv run pytest tests/test_seed.py -v` → PASS.
- [ ] **Step 6:** Запись в `CHANGELOG.md` (раздел `## 2026-10-22`), `git add -A && git commit -m "feat: каркас, справочники, схема БД и мок-поставщики"`.

### Task 2: Разбор запроса на правилах

**Files:** Create `app/ai/__init__.py`, `app/ai/base.py`, `app/ai/rules.py` (часть `RuleBasedQueryParser`); Test `tests/test_parser.py`

**Interfaces:**
- Consumes: `catalog.PRODUCTS/CITIES/CONDITIONS/CATEGORIES`, `Criteria`.
- Produces: `class QueryParser(Protocol): def parse(self, text: str, default_city: str | None = None) -> Criteria`; `RuleBasedQueryParser().parse(...)`.

- [ ] **Step 1: Тесты**

```python
P = RuleBasedQueryParser()
def test_full_example():
    c = P.parse("Нужна моцарелла для пиццы в Екатеринбург, ~200 кг в месяц, обязательно декларации, желательно отсрочка 14 дней")
    assert (c.product, c.category, c.city, c.volume_kg_month) == ("mozzarella","dairy","ekb",200)
    assert c.required == ["declarations"] and c.preferred == ["deferral"] and c.deferral_days == 14
def test_weekly_volume_tons_and_kz():
    c = P.parse("лосось охлаждённый Алматы 0,5 т в неделю, меркурий")
    assert (c.product, c.city) == ("salmon","almaty") and c.volume_kg_month == pytest.approx(2165, rel=0.01)
    assert c.required == ["mercury"]
def test_packaging_price_cap_and_case_forms():
    c = P.parse("контейнеры для роллов в Казани до 12 руб")
    assert (c.product, c.category, c.city, c.max_price_rub) == ("roll_container","packaging","kazan",12)
def test_category_only():
    c = P.parse("ищу поставщика рыбы и морепродуктов, Екб")
    assert c.product is None and c.category == "fish" and c.city == "ekb"
def test_nothing_understood_uses_default_city():
    c = P.parse("привет", default_city="ekb")
    assert c.category is None and c.city == "ekb" and c.unparsed == ["привет"]
def test_producer_only_and_halal():
    c = P.parse("куриное филе халяль только производители Тюмень")
    assert c.product == "chicken_fillet" and set(c.required) == {"halal","producer_only"}
```

- [ ] **Step 2:** `uv run pytest tests/test_parser.py -v` → FAIL.
- [ ] **Step 3: Реализовать.** Нижний регистр, `ё→е`. Товар — по основам слов (самая длинная совпавшая основа выигрывает; товар задаёт и категорию). Категория без товара — по основам названий категорий (`рыб`, `морепродукт`, `молоч`, `сыр`, `мяс`, `овощ`, `зелен`, `бакале`, `соус`, `специ`, `готов`, `полуфабрикат`, `хлеб`, `выпеч`, `напит`, `упаков`, `посуд`). Город — по основам; если нет → `default_city`. Объём: regex `(\d+[.,]?\d*)\s*(кг|т|тонн\w*)` + опционально `в (месяц|неделю|день)`; т ×1000; неделя ×4.33; день ×30. Цена: `до (\d+)\s*(₽|руб|р\b|тг|₸)` (₸ → /6.0). Условия: текст режется на сегменты по `,;`; сегмент со словами `желательно|по возможности|хорошо бы|плюсом` → preferred, иначе required. Отсрочка `(\d+)\s*(дн|дней|день)` в сегменте с «отсроч» → `deferral_days`. `unparsed` — сегменты, в которых не найдено ничего (кроме служебных слов «нужен/нужна/ищу/поставщик»).
- [ ] **Step 4:** тесты → PASS. **Step 5:** CHANGELOG + commit `feat: разбор запроса закупщика на правилах`.

### Task 3: Балл, части балла, флажки рисков

**Files:** Create `app/scoring.py`; Test `tests/test_scoring.py`

**Interfaces:**
- Produces: `haversine_km(lat1, lon1, lat2, lon2) -> float`; `WEIGHTS: dict[Mode, dict[str,int]]` = balance {match:30, reliability:30, price:20, logistics:20}, reliability {25,45,10,20}, price {25,20,40,15}, speed {25,20,15,40}; `best_offer(s: Supplier, c: Criteria) -> Product | None`; `risk_flags(s: Supplier, c: Criteria, offer: Product | None, distance_km: float, today: date) -> list[Flag]`; `is_critical(flags) -> bool`; `price_part(price_rub: float | None, median_rub: float | None) -> int`; `score_supplier(s, c, offer, distance_km, median_price_rub: float | None, mode: Mode, today: date) -> tuple[int, ScoreParts]`.

- [ ] **Step 1: Тесты** (фабрика `make_supplier(**overrides)` в `tests/conftest.py`, база — «хороший» производитель)

```python
def test_haversine_ekb_chelyabinsk(): assert 190 < haversine_km(56.8389,60.6057,55.1644,61.4368) < 200
def test_critical_flags():
    for over in [dict(founded=date(2026,6,1)), dict(tax_debt_rub=120000), dict(bankruptcy=True),
                 dict(linked_problem_company="ООО X"), dict(documents=[Document(kind="declaration",number="1",valid_until=date(2026,1,1))])]:
        assert is_critical(risk_flags(make_supplier(**over), C, None, 10, TODAY)), over
def test_warn_min_order_above_volume():
    fl = risk_flags(make_supplier(min_order_kg=300), Criteria(volume_kg_month=200), None, 10, TODAY)
    assert any(f.level=="warn" and "партия" in f.text for f in fl) and not is_critical(fl)
def test_no_price_neutral_and_flagged():
    s = make_supplier(); offer = Product(name="Моцарелла", key="mozzarella", category="dairy", price=None, price_rub=None, unit="кг")
    _, parts = score_supplier(s, C, offer, 10, 500, "balance", TODAY)
    assert parts.price == 50
    assert any("по запросу" in f.text for f in risk_flags(s, C, offer, 10, TODAY))
def test_price_monotonic(): # дешевле медианы → выше
    assert price_part(400, 500) > price_part(500, 500) > price_part(650, 500); assert price_part(400,500)==100 and price_part(650,500)==0
def test_closer_is_better_logistics():
    _, near = score_supplier(make_supplier(), C, None, 10, None, "balance", TODAY)
    _, far  = score_supplier(make_supplier(), C, None, 400, None, "balance", TODAY)
    assert near.logistics > far.logistics
def test_risky_reliability_lower_and_mode_changes_total(): ...  # linked_problem_company → reliability ≤ 40; mode "price" vs "reliability" дают разный total для дешёвого рискованного
def test_total_is_weighted_sum(): ...  # total == round(Σ part*w/100)
```

- [ ] **Step 2:** FAIL. **Step 3: Реализовать.** `price_part(price_rub, median)`: ratio ≤0.8 → 100, ≥1.3 → 0, линейно между; нет цены или медианы → 50. Остальные коэффициенты — на усмотрение исполнителя в рамках спеки §5 (соответствие: точный товар > только категория, мин. партия ≤ объёма, выполненные preferred; надёжность: возраст, производитель, действующие документы, Меркурий, ХАССП, минус суды/долг/банкротство/массовый адрес/связи, рейтинг; логистика: расстояние до ~600 км, срок доставки, своя доставка, холодовая цепь для `chilled`). Все части — int 0..100. Флажки: ok-флаги «производитель», «декларации до ММ.ГГГГ», «своя доставка», «−N% к медиане»; warn — «дистрибьютор», «мин. партия X кг больше вашей потребности», «доставка N дн.», «нет данных о документах», «цена по запросу»; bad — список из теста (тексты по-русски, с конкретикой: «компании 4 мес.», «связь с ООО «СырЛайн» (ликвидировано…)», «декларация истекла 01.2026»).
- [ ] **Step 4:** PASS. **Step 5:** CHANGELOG + commit `feat: балл поставщика и флажки рисков`.

### Task 4: Поиск

**Files:** Create `app/search.py`; Test `tests/test_search.py`

**Interfaces:**
- Consumes: `db.load_suppliers`, `scoring.*`, `catalog.CITIES`, Explainer (Task 5 добавит `reasons`; до этого `reasons=[]`).
- Produces: `class SupplierSource(Protocol): def candidates(self, c: Criteria) -> list[Supplier]` и `SqliteMockSource(conn)` (SQL-префильтр по товару/категории) — точка подмены мока на реальные источники; `run_search(conn, req: SearchRequest, profile: Profile, today: date, explainer: Explainer | None = None, source: SupplierSource | None = None) -> SearchResponse`; `origin_for(criteria, profile) -> tuple[float, float, str]`.

- [ ] **Step 1: Тесты** (на сид-базе из conftest)

```python
def test_demo_scenario_order(conn):
    r = run_search(conn, SearchRequest(criteria=PARSER.parse(DEMO_TEXT)), PROFILE, TODAY)
    names = [x.supplier.name for x in r.results]
    assert names[0] == "Уральская сыроварня"
    assert "ООО «СырПром»" not in names                       # hide_critical по умолчанию
    assert any(h.reason.startswith("Критичные риски") for h in r.hidden)
def test_show_critical_when_filter_off(conn): ...           # hide_critical=False → СырПром в списке с bad-флагом
def test_required_is_hard_filter(conn): ...                   # required=["halal"] → у всех результатов есть halal; hidden содержит «Нет: халяль»
def test_everything_filtered_returns_reasons(conn):
    c = Criteria(product="mozzarella", category="dairy", city="ekb", required=["halal","haccp","producer_only"], max_price_rub=1)
    r = run_search(conn, SearchRequest(criteria=c), PROFILE, TODAY)
    assert r.results == [] and sum(h.count for h in r.hidden) > 0
def test_other_city_origin(conn):
    r = run_search(conn, SearchRequest(criteria=Criteria(category="fish", city="almaty")), PROFILE, TODAY)
    assert r.origin["label"] == "Алматы (центр)" and r.results and r.results[0].supplier.city == "almaty"
    assert all(x.distance_km <= 300 or x.distance_km <= x.supplier.delivery_radius_km for x in r.results)
def test_no_price_not_hidden_by_price_cap_and_not_in_median(conn): ...
def test_sort_by_price_and_distance(conn): ...                 # sort="price": по offer.price_rub, None в конце
def test_nothing_parsed_returns_city_suppliers(conn): ...      # Criteria(city="ekb") → результаты есть, все ≤ radius
```

- [ ] **Step 2:** FAIL. **Step 3: Реализовать.** Origin: если `criteria.city` пуст или совпадает с `profile.city` → координаты профиля, label = адрес профиля; иначе центр города, label «<Город> (центр)». Кандидаты: SQL-префильтр по `products.key`/`products.category` (или все, если ни того ни другого). Порядок жёстких фильтров (каждый считает свою причину в `hidden`): «Далеко: дальше N км и не возят» (distance > max(radius_km, filters.max_distance_km or radius) и distance > delivery_radius_km), «Нет: <условие>» для каждого required, «Дороже N ₽» (только при известной цене), «Мин. партия больше потребности» (если `fit_min_order`), «Не тот тип», «Критичные риски» (если `hide_critical`). Медиана — по `offer.price_rub` прошедших фильтры (без None). Затем балл, сортировка, `reasons` от explainer.
- [ ] **Step 4:** PASS. **Step 5:** CHANGELOG + commit `feat: поиск с жёсткими фильтрами, причинами скрытия и сортировкой`.

### Task 5: Объяснения, вывод сравнения, письмо КП

**Files:** Modify `app/ai/base.py`, `app/ai/rules.py`; Test `tests/test_texts.py`

**Interfaces:**
- Produces: `class Explainer(Protocol): def reasons(self, item: ScoredSupplier, c: Criteria) -> list[str]; def compare(self, items: list[ScoredSupplier], c: Criteria) -> CompareResult`; `class KPWriter(Protocol): def draft(self, profile: Profile, c: Criteria, suppliers: list[Supplier]) -> KPDraft`; реализации `RuleBasedExplainer`, `RuleBasedKPWriter`. Подключить `RuleBasedExplainer` в `run_search` по умолчанию.

- [ ] **Step 1: Тесты**

```python
def test_reasons_short_and_specific(conn):  # 2–4 строки, есть цифры (₽/км/дн), без дублей флажков
def test_compare_best_and_conclusion(conn):
    res = EXPLAINER.compare([ural, put], Criteria(product="mozzarella", volume_kg_month=200))
    assert res.best["price"] == put.supplier.id and res.best["min_order"] == ural.supplier.id and res.best["score"] == ural.supplier.id
    assert "Уральская сыроварня" in res.conclusion and "300 кг" in res.conclusion
def test_kp_draft_contains_essentials():
    d = WRITER.draft(PROFILE, parse(DEMO_TEXT), [ural.supplier])
    for s in ["Сушкоф и пицца", "Моцарелла", "200 кг", "отсрочк", "деклараци", "образ", PROFILE.address]: assert s.lower() in d.body.lower()
    assert "Моцарелла" in d.subject
def test_kp_draft_without_product_uses_category(): ...   # Criteria(category="fish") → «Рыба и морепродукты» в теме
```

- [ ] **Step 2:** FAIL. **Step 3:** Реализовать. Строки сравнения (ключи `best`): `score`, `price`, `min_order`, `distance`, `delivery_days`, `deferral`, `reliability`. Вывод: «Для N кг/мес выгоднее X: …» + оговорка про дешевле, но партия/дистанция/риск у альтернативы. Письмо — по образцу из макета `kp-goulash-style.html` (приветствие, кто мы с числом точек, что нужно, объём, адрес, что прислать: цену за единицу при этом объёме, мин. партию, условия доставки и оплаты, документы из required/preferred, образец; контакт из профиля).
- [ ] **Step 4:** PASS. **Step 5:** CHANGELOG + commit `feat: объяснения «почему», вывод сравнения и черновик КП`.

### Task 6: HTTP API

**Files:** Create `app/api.py`, `app/main.py`; Modify `app/db.py` (notes, kp_requests, profile CRUD); Test `tests/test_api.py`

**Interfaces:**
- Produces (все JSON, ошибки — `{"detail": "<по-русски>"}`):
  - `GET /api/meta` → `{categories, cities, conditions, modes, examples: list[str]}` (4 примера запросов, первый — DEMO_TEXT)
  - `POST /api/parse {text}` → `Criteria` (default_city = профиль)
  - `POST /api/search SearchRequest` → `SearchResponse`
  - `GET /api/suppliers/{id}` → `{supplier: Supplier, notes: list[Note]}`; 404 «Поставщик не найден»
  - `POST /api/suppliers/{id}/notes {author, text}` → `Note` (text 1..2000, обрезка пробелов; пустой → 422)
  - `POST /api/compare {criteria, supplier_ids (2..4), mode}` → `CompareResult`; иначе 422 «Выберите от 2 до 4 поставщиков»
  - `POST /api/kp/draft {criteria, supplier_ids}` → `KPDraft`
  - `POST /api/kp {supplier_ids, channel, product_label}` → `list[KPRequest]` (status="sent")
  - `GET /api/kp` → `list[KPRequest]` (новые сверху; `days_waiting` = дни с created_at; статус "sent" старше 1 дня отдаётся как "waiting")
  - `PATCH /api/kp/{id} {status?, price_rub?, note?}` → `KPRequest`
  - `GET /api/profile`, `PUT /api/profile Profile` (lat/lon берутся из `CITIES[city]`, если адрес не геокодируется — без внешнего геокодера)
  - `POST /api/demo/reset` → `{ok: true}` (`seed_db`)
  - `GET /` → `static/index.html`; `/static/*`
- `create_app(db_path: str | None = None) -> FastAPI`; `app = create_app()` для uvicorn. Сид — также 3 демо-запроса КП (received 495 ₽, waiting 2 дн., sent) и 2 заметки (одна — «Дегустация 12.10 — тянется хорошо, 4/5 — Анна, шеф» у Уральской сыроварни).

- [ ] **Step 1: Тесты** (TestClient на `create_app(":memory:"` или tmp_path)`)

```python
def test_parse_then_search_flow(client): ...        # parse(DEMO_TEXT) → search → 200, первый — Уральская сыроварня
def test_supplier_404(client): assert client.get("/api/suppliers/99999").status_code == 404
def test_note_stored_verbatim(client):
    r = client.post(f"/api/suppliers/{SID}/notes", json={"author":"Тест","text":"<script>alert(1)</script>"})
    assert r.json()["text"] == "<script>alert(1)</script>"
def test_empty_note_422(client): ...
def test_compare_requires_2_to_4(client): ...       # 1 id → 422 с русским текстом
def test_kp_lifecycle(client): ...                  # draft → post (2 шт, status sent) → patch received + price → get отражает
def test_demo_reset_restores(client): ...           # добавили заметку → reset → заметки как в сиде
def test_index_served(client): assert "Goulash" in client.get("/").text
```

- [ ] **Step 2:** FAIL. **Step 3:** Реализовать. **Step 4:** PASS + `make run` и `curl -s localhost:8000/api/meta | head -c 300` → JSON с categories. **Step 5:** CHANGELOG + commit `feat: HTTP API сервиса`.

### Task 7: Фронт — каркас, стиль Goulash, поиск, список, фильтры, карта

**Files:** Create `static/index.html`, `static/styles.css`, `static/app.js`, `static/logo.svg`, `static/vendor/*` (скачать Alpine 3.14.1 `cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js` и Leaflet 1.9.4 `unpkg.com/leaflet@1.9.4/dist/` с images)

**Interfaces:**
- Consumes: API из Task 6.
- Produces: Alpine-компонент `app()` с состоянием `{view: 'search'|'requests'|'about', query, criteria, filters, mode, sort, layout: 'list'|'map'|'both', results, hidden, origin, selected: Set<id>, detail, compare, kp, profile}` и методами `parse()`, `search()`, `removeChip(kind, value)`, `toggleCondition(key)`, `openDetail(id)`, `toggleSelect(id)` — Task 8 добавляет модалки поверх.

- [ ] **Step 1:** Шапка: SVG-логотип (квадрат-знак с акцентом `#21FFFF`) + «Goulash.tech · Поставщики», навигация «Поиск / Запросы КП / Как это работает», бейдж «демо-данные», кнопка «Профиль». Пустое состояние: крупный заголовок «Найдите поставщика за минуту», поле запроса (textarea, Enter — искать, Shift+Enter — перенос), 4 примера-чипа из `/api/meta`.
- [ ] **Step 2:** После поиска: строка чипов критериев (каждый с ✕; required — обводка акцентом, preferred — пунктир; «не понял: …» — серым), переключатели режима (Баланс/Надёжность/Цена/Скорость), сортировки и вида (Список/Карта/Список+карта). Слева фильтры: радиус (слайдер 50–2000 км), макс. цена, чекбоксы условий (синхронны с required-чипами), «мин. партия ≤ моего объёма», «скрыть с критичными рисками» (вкл.), тип. Любое изменение → `search()` (debounce 250 мс).
- [ ] **Step 3:** Карточки результата: балл в акцентном квадрате (цвет по порогам ≥75 акцент, 50–74 warn, <50 bad), название, город · расстояние · срок, цена (₽; для KZ «₸ / ≈₽»), мин. партия, `reasons`, флажки ✓⚠⛔, чекбокс «в сравнение». Под списком — блок «Скрыто N: причины» с кнопкой «Показать скрытых с рисками» (выключает hide_critical). Пустой результат — текст «Под все условия никто не подошёл» + причины + совет.
- [ ] **Step 4:** Карта Leaflet (тёмная подложка: OSM tiles + CSS-фильтр `invert(1) hue-rotate(180deg) brightness(.9)` на `.leaflet-tile`), маркер ★ origin, круг радиуса, маркеры-кружки поставщиков цвета балла с номером; клик по маркеру → подсветка/скролл карточки и наоборот; `fitBounds` по результатам. При ошибке загрузки тайлов — надпись «Карта недоступна без интернета».
- [ ] **Step 5:** Все пользовательские/данные из API выводить через `x-text` (никаких `x-html`). Адаптив: <900px — фильтры в выезжающей панели, карта над списком.
- [ ] **Step 6: Проверка в браузере.** `make run`; открыть http://localhost:8000, ввести DEMO_TEXT → первым «Уральская сыроварня», на карте ★ и маркеры, «Скрыто: Критичные риски — 1+»; снять «скрыть рискованных» → СырПром с ⛔. Скриншот 1440px и 390px. Консоль без ошибок.
- [ ] **Step 7:** CHANGELOG + commit `feat: фронт — поиск, фильтры, список и карта в стиле Goulash`.

### Task 8: Фронт — карточка, сравнение, запрос КП, «Запросы КП», профиль, «Как это работает»

**Files:** Modify `static/index.html`, `static/app.js`, `static/styles.css`

**Interfaces:**
- Consumes: `app()` из Task 7, API из Task 6.

- [ ] **Step 1: Карточка** — правая выезжающая панель: заголовок + балл; реквизиты (юрлицо, ИНН/БИН, тип, год, сотрудники, выручка); контакты (телефон `tel:`, e-mail, сайт, WhatsApp, Telegram) с пометкой источника; расшифровка балла — 4 полосы с весами режима; ассортимент с ценами; условия; документы (срок, «истекла» красным); проверка компании; заметки + форма добавления; кнопки «В сравнение», «Запросить КП».
- [ ] **Step 2: Сравнение** — нижняя плашка «Выбрано N · Сравнить · Запросить КП» (активна при 2–4 / 1–N); модалка-таблица: строки Балл, Цена, Мин. партия, Расстояние, Срок доставки, Отсрочка, Надёжность, Документы, Риски; ячейка из `best` с ★ и акцентом; под таблицей `conclusion`; кнопка «Запросить КП у выбранных».
- [ ] **Step 3: Запрос КП** — модалка по макету `kp-goulash-style.html`: список выбранных (снять галочку можно), каналы E-mail / WhatsApp / Telegram / Скопировать, редактируемые тема и текст из `/api/kp/draft`. «Отправить»: для каждого поставщика открыть ссылку канала (`mailto:` с subject/body; `https://wa.me/<цифры>?text=`; `https://t.me/share/url?url=&text=`; copy — `navigator.clipboard`) — для нескольких поставщиков e-mail одним `mailto:` с адресами через запятую, мессенджеры — по одному окну на поставщика; затем `POST /api/kp` и тост «Запросы сохранены во вкладке «Запросы КП»».
- [ ] **Step 4: «Запросы КП»** — таблица: поставщик (клик → карточка), товар, канал, дата, статус (select: Отправлено/Ждём ответ/КП получено/Отказ, цветные пилюли), цена КП (input, ₽), заметка (input), «ждём N дн.»; сохранение по blur через PATCH; кнопка «Сравнить полученные КП» (берёт поставщиков со статусом received, ≥2).
- [ ] **Step 5: Профиль** — модалка: компания, город (select из `meta.cities`), адрес, контакт, телефон, e-mail, число точек; сохранить → PUT, повторить поиск. **«Как это работает»** — страница: 4 шага пути, формула балла и режимы, таблица «в прототипе → в проде» (мок → 2ГИС/DaData/Checko/ФНС/ФСА/ВетИС, правила → LLM), ссылка на `plan.md`; кнопка «Сбросить демо-данные» (POST reset, подтверждение через собственную модалку, не `confirm()`).
- [ ] **Step 6: Проверка в браузере** — сквозной сценарий: DEMO_TEXT → открыть карточку Уральской сыроварни, добавить заметку → выбрать её и «Молочный путь» → сравнение (★ у цены у «Молочного пути», вывод упоминает 300 кг) → «Запросить КП» (текст содержит «Сушкоф и пицца», «200 кг») → «Копировать» → вкладка «Запросы КП» показывает 2 новых + 3 демо; сменить статус на «КП получено», цену 495 → перезагрузить страницу → сохранилось. Скриншоты, консоль без ошибок.
- [ ] **Step 7:** CHANGELOG + commit `feat: фронт — карточка, сравнение, запрос КП, вкладка запросов, профиль`.

### Task 9: README, Docker, финальная проверка

**Files:** Create `README.md`, `Dockerfile`, `docker-compose.yml`, `.dockerignore`; Modify `CHANGELOG.md`

- [ ] **Step 1: README.md** — по-русски: что это и для кого (1 абзац, ссылка на `plan.md` и `docs/report.md`); скриншот(ы) в `docs/screenshots/`; запуск: `make install && make run` → http://localhost:8000 (и вариант `uv run uvicorn …`, `docker compose up`); сценарий демо в 5 шагов; как устроено (схема из спеки §6, балл §5); что мок, а что настоящее; как подключить реальные источники (интерфейс `SupplierSource`/`db`, таблица источников из `docs/report.md` §3.4) и LLM (новая реализация протоколов `QueryParser/Explainer/KPWriter` в `app/ai/`); обоснование выбора формата и стека; блок «Текст для HR» (5–7 предложений: ссылка на прототип localhost + репозиторий, что сделано, почему так).
- [ ] **Step 2:** `Dockerfile` (python:3.12-slim, uv, `uvicorn app.main:app --host 0.0.0.0 --port 8000`, `GOULASH_DB=/data/goulash.db`), `docker-compose.yml` (порт 8000, volume `./data:/data`). Docker локально не установлен — в README пометка, что образ не собирался на этой машине.
- [ ] **Step 3:** `make test` → все зелёные; `rm -rf data && make run` → база создаётся сама, сквозной сценарий из Task 8 Step 6 проходит.
- [ ] **Step 4:** CHANGELOG + commit `docs: README, Docker и пояснение для HR`.

---

## UX-дополнения (спека §11) — распределение по задачам

**Task 1 (модели и сид):**
- `Supplier`: добавить `data_as_of: date` (сид: 2026-10-14..2026-10-21), сделать `deferral_days: int | None`, `delivery_schedule: str | None`, `email: str | None`, `phone: str | None`; `preferred_channel: Literal["whatsapp","email","telegram"] | None` (None — нет контактов).
- `Flag`: добавить `hint: str | None = None` (за что выдан).
- `Criteria`: добавить `suggestions: list[Condition] = []`.
- `ScoredSupplier`: добавить `logistics_text: str`.
- Новые модели: `FactGroup(title: str, level: Literal["ok","warn","bad"], facts: list[Flag])`; `ReliabilityReport(verdict: Literal["high","medium","low"], counts: dict[str,int], groups: list[FactGroup], as_of: date)`; `RelaxHint(condition: Condition, label: str, extra: int)`; `KPEvent(at: datetime, status: KPStatus, text: str)`; в `KPRequest` — `events: list[KPEvent]`, `reply_due: date` (created + 3 дня).
- `SearchResponse`: добавить `facets: dict[str, int]` (сколько из показанных удовлетворяют каждому условию + `fit_min_order`, `producer`), `relax: list[RelaxHint]`.
- Сид: ≥2 поставщика без контактов (`preferred_channel=None`), ≥1 с декларацией, истекающей 2026-11-10, ≥10 с `deferral_days=None` или `delivery_schedule=None`. Тест: `test_seed_ux_cases`.

**Task 2 (разбор):** `suggestions` — только то, чего нет в required/preferred: `mercury` для категорий dairy/meat/fish; `cold_chain` для `chilled` товаров. Тест: `parse("моцарелла Екб 200 кг")` → `required == []`, `"mercury" in suggestions`, `"cold_chain" in suggestions`; на DEMO_TEXT `"declarations" not in suggestions`.

**Task 3 (балл и флажки):**
- Документ с `valid_until - today ≤ 30` дн. → warn «декларация истекает через N дн.»; тест `test_doc_expiring_warn` (valid_until=2026-11-10 → «через 19 дн.», не critical).
- Все ok/warn/bad-флаги — с `hint` (например «Производитель: основной ОКВЭД 10.51, заявитель в декларациях — изготовитель (демо)»). Тест: у всех флагов `hint` непустой.
- `reliability_report(s: Supplier, today: date) -> ReliabilityReport`: 5 групп из спеки §11 п. 2; уровень группы = худший факт; verdict: есть bad → low, warn ≥ 2 → medium, иначе high. Тесты: СырПром → low и counts["bad"] ≥ 1; Уральская сыроварня → high; `as_of == s.data_as_of`.
- `logistics_text(s, distance_km) -> str`: «38 км · 1–2 дня · своя доставка» (срок «N дн.» → «1 день» / «1–2 дня» / «N дн.»; «самовывоз/ТК», если нет своей доставки; « · холод» при cold_chain). Тест на две формы.
- `None` в `deferral_days` → флажок не ставится, балл соответствия по preferred=deferral не начисляется.

**Task 4 (поиск):** `facets` считаются по итоговым результатам; `relax` — для каждого required-условия: сколько **дополнительных** поставщиков появится, если перенести его в preferred (повторный прогон фильтров без него; `extra > 0` только). Тест: `required=["deferral","halal"]` → в `relax` есть halal с extra > 0; `facets["declarations"] <= len(results)`.

**Task 5 (тексты):** письмо КП добавляет блок «Также уточните: …» с пунктами, которых нет у выбранных поставщиков (`deferral_days is None` → «условия отсрочки», `delivery_schedule is None` → «график доставки»). Тест на поставщика с `deferral_days=None`.

**Task 6 (API):** `POST /api/kp` принимает `{items: [{supplier_id, channel}], product_label}`; создаёт событие «Отправлено через <канал>». `PATCH /api/kp/{id}` со сменой статуса добавляет событие. `GET /api/suppliers/{id}` возвращает также `reliability: ReliabilityReport`. Тесты: события растут после PATCH; `reply_due` = created + 3 дня; поставщик без контактов в `items` с channel≠"copy" → 422 «У поставщика нет контактов для этого канала».

**Task 7 (фронт: поиск):** две колонки «Почему он» (reasons + ok-флаги, ≤4) и «На что обратить внимание» (warn/bad); бейджи с `title`/тултипом из `hint`; счётчики в подписях фильтров из `facets`; пустой/скудный результат показывает `relax` кнопками «сделать желательным (+N)»; `logistics_text` под названием и в попапе маркера; пунктирные чипы `suggestions` («+ Меркурий?») — клик добавляет в preferred.

**Task 8 (фронт: остальное):** карточка — плашка вердикта надёжности, вкладки «Все · ⛔ N · ⚠ N · ✓ N», группы фактов, «данные на …», источник у каждого блока, «Нет данных — уточнить в КП» для пустых полей; нижняя панель «Выбрано N из 4» с предупреждением «у X нет контактов — только копирование»; модалка КП — канал построчно (по умолчанию `preferred_channel`); «Запросы КП» — шкала статусов (Отправлено → Ждём ответ → КП получено / Отказ), раскрывающаяся лента событий, «ждём ответ до ДД.ММ».
