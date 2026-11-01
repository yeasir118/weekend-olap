"""Fill the OLTP source database with ~10 months of realistic e-commerce history.

Plays the role of the application: users sign up, place orders, pay; a few products
change price or get discontinued along the way. Timestamps are backdated so the
warehouse has history to analyse.

Deterministic (fixed random seed) and destructive: all source tables are truncated first.

    .venv\\Scripts\\python postgres-source\\seed.py
"""

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
from faker import Faker

from db import connect

RANDOM_SEED = 42
N_USERS = 200
N_PRODUCTS = 50
N_ORDERS = 1000

HISTORY_START = datetime(2026, 1, 1, tzinfo=UTC)
HISTORY_END = datetime(2026, 10, 31, 23, 59, tzinfo=UTC)

# Must match data/marketing_campaigns.csv. An order can only be attributed to a campaign
# that had already started.
CAMPAIGN_STARTS = {
    1: datetime(2026, 1, 1, tzinfo=UTC),
    2: datetime(2026, 1, 5, tzinfo=UTC),
    3: datetime(2026, 2, 1, tzinfo=UTC),
}
CAMPAIGN_SHARE = 0.3

# category -> unit price range in cents
CATEGORIES = {
    "electronics": (2_000, 60_000),
    "home": (800, 15_000),
    "books": (500, 4_000),
    "clothing": (1_200, 12_000),
    "sports": (1_500, 25_000),
}
COUNTRIES = ["Bangladesh", "India", "United States", "United Kingdom", "Germany", "Canada"]
COUNTRY_WEIGHTS = [35, 20, 20, 10, 8, 7]
PAYMENT_METHODS = ["card", "paypal", "bank_transfer"]
PAYMENT_WEIGHTS = [70, 20, 10]


def random_ts(start: datetime, end: datetime) -> datetime:
    return start + timedelta(seconds=random.uniform(0, (end - start).total_seconds()))


def money(cents: int) -> Decimal:
    return Decimal(cents) / 100


def build_users(fake: Faker) -> list[dict]:
    users = []
    for _ in range(N_USERS):
        created = random_ts(datetime(2025, 6, 1, tzinfo=UTC), datetime(2026, 10, 15, tzinfo=UTC))
        users.append({
            "email": fake.unique.email(),
            "first_name": fake.first_name(),
            "last_name": fake.last_name(),
            "city": fake.city(),
            "country": random.choices(COUNTRIES, COUNTRY_WEIGHTS)[0],
            "created_at": created,
            "updated_at": created,
            "deleted_at": None,
            # A few customers buy a lot, most buy rarely (long tail), like real shops.
            "order_weight": random.paretovariate(2.5),
        })
    users.sort(key=lambda u: u["created_at"])
    for user_id, user in enumerate(users, start=1):
        user["id"] = user_id
    return users


def build_products(fake: Faker) -> list[dict]:
    products = []
    for _ in range(N_PRODUCTS):
        category = random.choice(list(CATEGORIES))
        created = random_ts(datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 12, 31, tzinfo=UTC))
        price = money(random.randint(*CATEGORIES[category]))
        products.append({
            "sku": fake.unique.bothify("SKU-####-??").upper(),
            "name": f"{fake.color_name()} {fake.word().capitalize()} {category.rstrip('s').title()}",
            "category": category,
            "unit_price": price,
            "original_price": price,
            "price_changed_at": None,
            "created_at": created,
            "updated_at": created,
            "deleted_at": None,
        })
    products.sort(key=lambda p: p["created_at"])
    for product_id, product in enumerate(products, start=1):
        product["id"] = product_id

    # Price changes: orders before the change keep the old price in order_items.unit_price.
    changed = random.sample(products, 8)
    for product in changed:
        product["price_changed_at"] = random_ts(datetime(2026, 6, 1, tzinfo=UTC), HISTORY_END)
        factor = Decimal(str(round(random.uniform(0.8, 1.25), 2)))
        product["unit_price"] = (product["original_price"] * factor).quantize(Decimal("0.01"))
        product["updated_at"] = product["price_changed_at"]

    # Discontinued (soft-deleted) products: still referenced by historical orders.
    discontinued = random.sample([p for p in products if p not in changed], 2)
    for product in discontinued:
        product["deleted_at"] = random_ts(datetime(2026, 9, 1, tzinfo=UTC), HISTORY_END)
        product["updated_at"] = product["deleted_at"]

    return products


def order_status(created: datetime) -> str:
    """Older orders have progressed further through the lifecycle."""
    if random.random() < 0.08:
        return "cancelled"
    age_days = (HISTORY_END - created).days
    if age_days < 2:
        return random.choice(["pending", "paid"])
    if age_days < 7:
        return random.choice(["paid", "shipped"])
    return "shipped"


def status_updated_at(status: str, created: datetime) -> datetime:
    """When the order last changed: the time of its latest status transition."""
    delay = {
        "pending": timedelta(0),
        "paid": timedelta(minutes=random.randint(1, 10)),
        "shipped": timedelta(days=random.randint(1, 4)),
        "cancelled": timedelta(hours=random.randint(1, 48)),
    }[status]
    return min(created + delay, HISTORY_END)


def build_orders(users: list[dict], products: list[dict]) -> tuple[list, list, list]:
    weights = [u["order_weight"] for u in users]
    orders = []
    for _ in range(N_ORDERS):
        user = random.choices(users, weights)[0]
        created = random_ts(max(HISTORY_START, user["created_at"]), HISTORY_END)
        status = order_status(created)

        campaign_id = None
        if random.random() < CAMPAIGN_SHARE:
            started = [cid for cid, start in CAMPAIGN_STARTS.items() if start <= created]
            campaign_id = random.choice(started) if started else None

        orders.append({
            "user_id": user["id"],
            "campaign_id": campaign_id,
            "status": status,
            "created_at": created,
            "updated_at": status_updated_at(status, created),
            "deleted_at": None,
        })

    # Ids increase with time, as they would in a real application.
    orders.sort(key=lambda o: o["created_at"])

    items, payments = [], []
    for order_id, order in enumerate(orders, start=1):
        order["id"] = order_id
        created = order["created_at"]
        available = [p for p in products if p["deleted_at"] is None or p["deleted_at"] > created]

        total = Decimal("0")
        for product in random.sample(available, random.randint(1, 5)):
            quantity = random.choices([1, 2, 3, 4], [70, 20, 7, 3])[0]
            changed_at = product["price_changed_at"]
            price = product["original_price"] if changed_at and created < changed_at else product["unit_price"]
            total += price * quantity
            items.append({
                "order_id": order_id,
                "product_id": product["id"],
                "quantity": quantity,
                "unit_price": price,
                "created_at": created,
                "updated_at": created,
                "deleted_at": None,
            })
        order["total_amount"] = total

        if order["status"] in ("paid", "shipped"):
            paid_at = created + timedelta(minutes=random.randint(1, 10))
            payments.append({
                "order_id": order_id,
                "method": random.choices(PAYMENT_METHODS, PAYMENT_WEIGHTS)[0],
                "amount": total,
                "created_at": paid_at,
                "updated_at": paid_at,
                "deleted_at": None,
            })

    for item_id, item in enumerate(items, start=1):
        item["id"] = item_id
    for payment_id, payment in enumerate(payments, start=1):
        payment["id"] = payment_id
    return orders, items, payments


def insert(cur: psycopg.Cursor, table: str, columns: list[str], rows: list[dict]) -> None:
    placeholders = ", ".join(f"%({c})s" for c in columns)
    cur.executemany(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
        rows,
    )


def main() -> None:
    random.seed(RANDOM_SEED)
    Faker.seed(RANDOM_SEED)
    fake = Faker()

    users = build_users(fake)
    products = build_products(fake)
    orders, items, payments = build_orders(users, products)

    timestamps = ["created_at", "updated_at", "deleted_at"]

    # One transaction: the database is either fully re-seeded or left as it was.
    with connect() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE payments, order_items, orders, products, users RESTART IDENTITY")

        insert(cur, "users", ["id", "email", "first_name", "last_name", "city", "country", *timestamps], users)
        insert(cur, "products", ["id", "sku", "name", "category", "unit_price", *timestamps], products)
        insert(cur, "orders", ["id", "user_id", "campaign_id", "status", "total_amount", *timestamps], orders)
        insert(cur, "order_items", ["id", "order_id", "product_id", "quantity", "unit_price", *timestamps], items)
        insert(cur, "payments", ["id", "order_id", "method", "amount", *timestamps], payments)

        # Ids were supplied explicitly, so move each identity sequence past them.
        # Otherwise the app's next insert (simulate_changes.py) collides with id 1.
        for table in ("users", "products", "orders", "order_items", "payments"):
            cur.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), (SELECT max(id) FROM {table}))")

    print(f"Seeded {len(users)} users, {len(products)} products, {len(orders)} orders, "
          f"{len(items)} order items, {len(payments)} payments")


if __name__ == "__main__":
    main()
