"""Детерминированный генератор демо-базы поставщиков.

Все компании, ИНН/БИН, телефоны и адреса выдуманы. Домены — *.example.
Три «героя» демо-сценария (моцарелла в Екатеринбурге) заданы вручную,
остальные поставщики генерируются из справочников с фиксированным seed.
"""
import random
import sqlite3
from datetime import date, timedelta

from .catalog import ANIMAL_CATEGORIES, CATEGORIES, CITIES, KZT_PER_RUB, PRODUCTS
from .db import insert_supplier, save_profile
from .models import Document, Product, Profile, Supplier

GENERATED_ON = date(2026, 10, 22)

DEFAULT_PROFILE = Profile(
    company="Сушкоф и пицца",
    city="ekb",
    address="Екатеринбург, ул. Малышева, 51 (кухня)",
    lat=56.8380,
    lon=60.6150,
    contact_name="Владимир, отдел закупок",
    phone="+7 343 200-00-00",
    email="zakupki@sushkof.example",
    points=26,
)

# Сколько сгенерированных поставщиков на город (плюс 3 героя: 2 в Екб, 1 в Челябинске).
CITY_COUNTS = {"ekb": 30, "chelyabinsk": 10, "tyumen": 8, "perm": 8, "kazan": 9,
               "moscow": 10, "almaty": 9, "astana": 6}

PHONE_CODES = {"ekb": "343", "chelyabinsk": "351", "tyumen": "3452", "perm": "342",
               "kazan": "843", "moscow": "495", "almaty": "727", "astana": "7172"}

NAME_TEMPLATES = {
    "dairy": ["Сыроварня «{w}»", "Молочный двор «{w}»", "{w} Молоко", "Сырный дом «{w}»"],
    "meat": ["Мясокомбинат «{w}»", "Птицефабрика «{w}»", "{w} Мит", "Ферма «{w}»"],
    "fish": ["Рыбный дом «{w}»", "{w} Фиш", "Морепродукты «{w}»"],
    "veg": ["Овощебаза «{w}»", "Агрокомплекс «{w}»", "Фабрика нарезки «{w}»"],
    "grocery": ["ТД «{w}»", "Бакалея «{w}»", "{w} Трейд"],
    "sauces": ["Соусная фабрика «{w}»", "{w} Соус", "ТД «{w} Вкус»"],
    "ready": ["Фабрика-кухня «{w}»", "Кухня «{w}»", "{w} Фуд"],
    "bakery": ["Хлебозавод «{w}»", "Пекарня «{w}»", "{w} Бейкери"],
    "drinks": ["Напитки «{w}»", "{w} Дринк", "Морсоварня «{w}»"],
    "packaging": ["Упаковка «{w}»", "{w} Пак", "ПолиПак «{w}»"],
}
WORDS_RU = ["Урал", "Исеть", "Север", "Восток", "Кедр", "Вектор", "Гранд", "Сибирь", "Волга",
            "Алтай", "Тайга", "Прайм", "Солнечный", "Нива", "Луч", "Берёзка", "Самоцвет",
            "Кристалл", "Ладога", "Байкал", "Чусовая", "Кама", "Тобол", "Пышма", "Магнит-Агро",
            "Шарташ", "Малахит", "Зауралье", "Свияга", "Медведь"]
WORDS_KZ = ["Арман", "Жетысу", "Нур", "Тянь-Шань", "Каспий", "Алатау", "Есиль", "Сарыарка",
            "Достык", "Шанырак", "Береке", "Самал"]
STREETS = ["Промышленная", "Заводская", "Складская", "Монтажников", "Фронтовых бригад",
           "Энтузиастов", "Сибирский тракт", "Окружная", "Рабочая", "Логистическая"]
TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
    "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
})

# Распределители чаще всего возят «сухие» категории пачкой.
DISTRIBUTOR_BUNDLES = [["grocery", "sauces"], ["grocery", "packaging"], ["dairy", "meat"],
                       ["fish", "grocery"], ["drinks", "packaging"], ["sauces", "packaging", "grocery"]]

SOURCES_RU = {"contacts": "2ГИС (демо)", "legal": "ЕГРЮЛ / ФНС (демо)",
              "documents": "Росаккредитация, ФСА (демо)", "prices": "прайс на сайте (демо)",
              "logistics": "сайт поставщика (демо)", "reviews": "отзывы 2ГИС (демо)"}
SOURCES_KZ = {"contacts": "2ГИС Казахстан (демо)", "legal": "stat.gov.kz / КГД (демо)",
              "documents": "реестр ЕАЭС (демо)", "prices": "прайс на сайте (демо)",
              "logistics": "сайт поставщика (демо)", "reviews": "отзывы 2ГИС (демо)"}


def _slug(name: str) -> str:
    core = name.split("«")[-1].split("»")[0] if "«" in name else name
    return "".join(ch for ch in core.lower().translate(TRANSLIT) if ch.isalnum())[:16] or "supplier"


def _price(rnd: random.Random, spec_key: str, currency: str, none_share: float = 0.1) -> tuple[float | None, float | None]:
    lo, hi = PRODUCTS[spec_key].price_rub
    if rnd.random() < none_share:
        return None, None
    rub = round(rnd.uniform(lo, hi), 0 if hi > 50 else 1)
    if currency == "KZT":
        kzt = round(rub * KZT_PER_RUB)
        return float(kzt), kzt / KZT_PER_RUB
    return float(rub), float(rub)


def _products_for(rnd: random.Random, categories: list[str], currency: str) -> list[Product]:
    result = []
    for cat in categories:
        keys = [k for k, p in PRODUCTS.items() if p.category == cat]
        picked = rnd.sample(keys, k=min(len(keys), rnd.randint(2, 4)))
        if cat == "dairy" and "mozzarella" not in picked:
            picked[0] = "mozzarella"   # почти у всех молочных поставщиков для пиццерий есть моцарелла
        for k in picked:
            price, price_rub = _price(rnd, k, currency)
            spec = PRODUCTS[k]
            result.append(Product(name=spec.name, key=k, category=cat, price=price, price_rub=price_rub, unit=spec.unit))
    return result


def _documents_for(rnd: random.Random, categories: list[str]) -> list[Document]:
    docs = [Document(kind="declaration", number=f"ЕАЭС N RU Д-{rnd.randint(10**7, 10**8 - 1)}",
                     valid_until=date(2027, 1, 1) + timedelta(days=rnd.randint(0, 700)))]
    if set(categories) & ANIMAL_CATEGORIES and rnd.random() < 0.85:
        docs.append(Document(kind="mercury", number=None, valid_until=None))
    if rnd.random() < 0.4:
        docs.append(Document(kind="haccp", number=f"ХАССП-{rnd.randint(1000, 9999)}",
                             valid_until=date(2027, 6, 1) + timedelta(days=rnd.randint(0, 500))))
    if set(categories) & {"meat", "ready"} and rnd.random() < 0.35:
        docs.append(Document(kind="halal", number=f"HALAL-{rnd.randint(100, 999)}", valid_until=None))
    if set(categories) & {"dairy", "drinks"} and rnd.random() < 0.8:
        docs.append(Document(kind="chestny_znak", number=None, valid_until=None))
    return docs


def _heroes() -> list[Supplier]:
    common = dict(country="RU", currency="RUB", prepay_required=False, mass_address=False,
                  bankruptcy=False, tax_debt_rub=0, website=None, telegram=None,
                  data_as_of=date(2026, 10, 21), sources=SOURCES_RU, categories=["dairy"])
    ural = Supplier(
        id=1, name="Уральская сыроварня", legal_name="ООО «Уральская сыроварня»", inn="6670412233",
        city="ekb", address="Екатеринбург, ул. Монтажников, 4", lat=56.9200, lon=60.5200,
        type="producer", founded=date(2016, 4, 12), employees=85, revenue_mln_rub=210.0,
        phone="+7 343 311-45-20", email="opt@uralsyr.example", whatsapp="+79221114520",
        preferred_channel="whatsapp", min_order_kg=50, own_delivery=True, cold_chain=True,
        delivery_days=1, delivery_radius_km=250, delivery_schedule="вт, пт", deferral_days=7,
        rating=4.8, reviews_count=64, lawsuits=0, linked_problem_company=None,
        products=[Product(name="Моцарелла", key="mozzarella", category="dairy", price=495, price_rub=495, unit="кг"),
                  Product(name="Сливочный сыр", key="cream_cheese", category="dairy", price=780, price_rub=780, unit="кг"),
                  Product(name="Сметана", key="sour_cream", category="dairy", price=210, price_rub=210, unit="кг")],
        documents=[Document(kind="declaration", number="ЕАЭС N RU Д-RU.РА01.В.48211", valid_until=date(2027, 3, 31)),
                   Document(kind="mercury", number=None, valid_until=None),
                   Document(kind="haccp", number="ХАССП-2231", valid_until=date(2027, 9, 30)),
                   Document(kind="chestny_znak", number=None, valid_until=None)],
        **{**common, "website": "https://uralsyr.example"},
    )
    put = Supplier(
        id=2, name="ТД «Молочный путь»", legal_name="ООО «ТД Молочный путь»", inn="7451120987",
        city="chelyabinsk", address="Челябинск, ул. Складская, 12", lat=55.2100, lon=61.3500,
        type="distributor", founded=date(2012, 9, 3), employees=140, revenue_mln_rub=640.0,
        phone="+7 351 220-10-10", email="sales@molput.example", whatsapp="+79080001010",
        preferred_channel="email", min_order_kg=300, own_delivery=True, cold_chain=True,
        delivery_days=2, delivery_radius_km=600, delivery_schedule="пн, ср, пт", deferral_days=14,
        rating=4.4, reviews_count=38, lawsuits=2, linked_problem_company=None,
        products=[Product(name="Моцарелла", key="mozzarella", category="dairy", price=455, price_rub=455, unit="кг"),
                  Product(name="Сливки 33%", key="cream", category="dairy", price=410, price_rub=410, unit="л"),
                  Product(name="Пармезан", key="parmesan", category="dairy", price=1490, price_rub=1490, unit="кг")],
        documents=[Document(kind="declaration", number="ЕАЭС N RU Д-RU.РА02.В.77120", valid_until=date(2027, 6, 30)),
                   Document(kind="mercury", number=None, valid_until=None),
                   Document(kind="chestny_znak", number=None, valid_until=None)],
        **common,
    )
    syrprom = Supplier(
        id=3, name="ООО «СырПром»", legal_name="ООО «СырПром»", inn="6686140001",
        city="ekb", address="Екатеринбург, ул. Энтузиастов, 30", lat=56.8550, lon=60.6600,
        type="producer", founded=date(2026, 6, 15), employees=6, revenue_mln_rub=None,
        phone="+7 343 300-00-13", email="info@syrprom.example", whatsapp=None,
        preferred_channel="email", min_order_kg=100, own_delivery=True, cold_chain=False,
        delivery_days=1, delivery_radius_km=80, delivery_schedule=None, deferral_days=None,
        rating=None, reviews_count=0, lawsuits=0,
        linked_problem_company="ООО «СырЛайн» (ликвидировано в 2025 г., санкции Роспотребнадзора)",
        products=[Product(name="Моцарелла", key="mozzarella", category="dairy", price=None, price_rub=None, unit="кг")],
        documents=[Document(kind="declaration", number="ЕАЭС N RU Д-RU.РА05.В.10002", valid_until=date(2027, 7, 1))],
        **common,
    )
    return [ural, put, syrprom]


def generate_suppliers(seed: int = 42) -> list[Supplier]:
    rnd = random.Random(seed)
    suppliers = _heroes()
    used_names = {s.name for s in suppliers}
    cat_keys = list(CATEGORIES)
    next_id = len(suppliers) + 1
    for city_key, count in CITY_COUNTS.items():
        city = CITIES[city_key]
        kz = city.country == "KZ"
        words = WORDS_KZ if kz else WORDS_RU
        for _ in range(count):
            distributor = rnd.random() < 0.3
            if distributor:
                categories = list(rnd.choice(DISTRIBUTOR_BUNDLES))
            else:
                categories = [cat_keys[next_id % len(cat_keys)]]
            name = rnd.choice(NAME_TEMPLATES[categories[0]]).format(w=rnd.choice(words))
            while name in used_names:
                name = rnd.choice(NAME_TEMPLATES[categories[0]]).format(w=rnd.choice(words)) + f" {rnd.randint(2, 9)}"
            used_names.add(name)
            slug = _slug(name)
            currency = "KZT" if kz else "RUB"
            whatsapp = f"+7{rnd.randint(700, 799) if kz else rnd.randint(900, 999)}{rnd.randint(1000000, 9999999)}" if rnd.random() < 0.7 else None
            telegram = f"@{slug}_opt" if rnd.random() < 0.3 else None
            email = f"opt@{slug}.example"
            own_delivery = rnd.random() < (0.8 if distributor else 0.6)
            founded = date(rnd.randint(2004, 2023), rnd.randint(1, 12), rnd.randint(1, 28))
            s = Supplier(
                id=next_id, name=name,
                legal_name=(f"ТОО «{name.split('«')[-1].rstrip('»')}»" if kz else f"ООО «{name.split('«')[-1].rstrip('»')}»"),
                inn=str(rnd.randint(10**11, 10**12 - 1)) if kz else str(rnd.randint(10**9, 10**10 - 1)),
                country=city.country, city=city_key,
                address=f"{city.name}, ул. {rnd.choice(STREETS)}, {rnd.randint(1, 120)}",
                lat=round(city.lat + rnd.uniform(-0.08, 0.08), 5),
                lon=round(city.lon + rnd.uniform(-0.12, 0.12), 5),
                type="distributor" if distributor else "producer", founded=founded,
                employees=rnd.randint(8, 400), revenue_mln_rub=round(rnd.uniform(15, 900), 1) if rnd.random() < 0.85 else None,
                phone=f"+7 {PHONE_CODES[city_key]} {rnd.randint(200, 399)}-{rnd.randint(10, 99)}-{rnd.randint(10, 99)}",
                email=email, website=f"https://{slug}.example" if rnd.random() < 0.6 else None,
                whatsapp=whatsapp, telegram=telegram,
                preferred_channel="whatsapp" if whatsapp else "email",
                min_order_kg=rnd.choice([20, 30, 50, 100, 150, 200, 300, 500]),
                own_delivery=own_delivery, cold_chain=own_delivery and bool(set(categories) & {"dairy", "meat", "fish", "ready", "veg"}),
                delivery_days=rnd.randint(1, 2) if own_delivery else rnd.randint(2, 4),
                delivery_radius_km=rnd.randint(300, 2500) if distributor else rnd.randint(50, 400),
                delivery_schedule=None if rnd.random() < 0.15 else rnd.choice(["ежедневно", "пн–сб", "пн, ср, пт", "вт, чт, сб"]),
                deferral_days=None if rnd.random() < 0.18 else rnd.choice([0, 0, 7, 7, 14, 21, 30]),
                prepay_required=rnd.random() < 0.2,
                currency=currency,
                rating=round(rnd.uniform(3.6, 5.0), 1) if rnd.random() < 0.85 else None,
                reviews_count=rnd.randint(0, 120),
                lawsuits=rnd.choice([0, 0, 0, 1, 2, 5]),
                tax_debt_rub=0, bankruptcy=False, mass_address=rnd.random() < 0.06,
                linked_problem_company=None,
                data_as_of=GENERATED_ON - timedelta(days=rnd.randint(1, 8)),
                sources=SOURCES_KZ if kz else SOURCES_RU,
                categories=categories,
                products=_products_for(rnd, categories, currency),
                documents=_documents_for(rnd, categories),
            )
            suppliers.append(s)
            next_id += 1
    _inject_risks(rnd, suppliers)
    return suppliers


def _inject_risks(rnd: random.Random, suppliers: list[Supplier]) -> None:
    """Детерминированно делаем часть поставщиков «плохими», чтобы флажки было видно."""
    pool = [s for s in suppliers if s.id > 3]
    picked = rnd.sample(pool, k=12)
    picked[0].tax_debt_rub = 184_000
    picked[1].tax_debt_rub = 46_500
    picked[2].bankruptcy = True
    picked[3].linked_problem_company = "ООО «ВкусЛюкс» (исключено из ЕГРЮЛ, долги перед ФНС)"
    picked[4].founded = date(2026, 7, 1)
    picked[5].documents[0].valid_until = date(2026, 1, 31)        # декларация истекла
    picked[6].documents[0].valid_until = date(2026, 8, 15)        # декларация истекла
    picked[7].documents[0].valid_until = date(2026, 11, 10)       # истекает меньше чем через 30 дней
    for s in picked[8:10]:                                        # нет контактов для запроса КП
        s.email = s.whatsapp = s.telegram = None
        s.preferred_channel = None
    picked[10].mass_address = True
    picked[11].lawsuits = 7


def seed_db(conn: sqlite3.Connection) -> None:
    for table in ("kp_events", "kp_requests", "notes", "documents", "products", "suppliers", "profile"):
        conn.execute(f"DELETE FROM {table}")
    for s in generate_suppliers():
        insert_supplier(conn, s)
    save_profile(conn, DEFAULT_PROFILE)
    conn.commit()
