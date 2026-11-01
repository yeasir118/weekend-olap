"""Simulate a burst of live application activity on the OLTP source database.

Each run makes a different, random batch of changes "now", the kind incremental sync
has to pick up:

    inserts       new users, new orders (with items and payments)
    updates       users move city, orders advance pending -> paid -> shipped, a price changes
    soft delete   one user gets deleted_at set           (visible to updated_at sync)
    hard delete   one cancelled order is purged with its items  (invisible to updated_at sync)

Prints the affected ids so you can check them in Bronze after the next sync (OLAP-404).

    .venv\\Scripts\\python postgres-source\\simulate_changes.py
"""

import random
import uuid
from decimal import Decimal

import psycopg
from faker import Faker

from db import connect
from seed import COUNTRIES, COUNTRY_WEIGHTS, PAYMENT_METHODS, PAYMENT_WEIGHTS

N_NEW_USERS = 3
N_NEW_ORDERS = 5
N_CITY_CHANGES = 2
N_ORDERS_ADVANCED = 3


def insert_payment(cur: psycopg.Cursor, order_id: int, amount: Decimal) -> None:
    cur.execute(
        "INSERT INTO payments (order_id, method, amount) VALUES (%s, %s, %s)",
        (order_id, random.choices(PAYMENT_METHODS, PAYMENT_WEIGHTS)[0], amount),
    )


def advance_orders(cur: psycopg.Cursor) -> list[str]:
    """Move open orders one step through their lifecycle, as fulfilment would."""
    cur.execute(
        """
        SELECT id, status, total_amount FROM orders
        WHERE status IN ('pending', 'paid') AND deleted_at IS NULL
        ORDER BY random() LIMIT %s
        """,
        (N_ORDERS_ADVANCED,),
    )
    advanced = []
    for order_id, status, total in cur.fetchall():
        new_status = "paid" if status == "pending" else "shipped"
        cur.execute("UPDATE orders SET status = %s WHERE id = %s", (new_status, order_id))
        if new_status == "paid":
            insert_payment(cur, order_id, total)
        advanced.append(f"{order_id} ({status} -> {new_status})")
    return advanced


def change_cities(cur: psycopg.Cursor, fake: Faker) -> list[int]:
    cur.execute(
        "SELECT id FROM users WHERE deleted_at IS NULL ORDER BY random() LIMIT %s",
        (N_CITY_CHANGES,),
    )
    user_ids = [row[0] for row in cur.fetchall()]
    for user_id in user_ids:
        # updated_at is deliberately not set here: the trigger does it.
        cur.execute("UPDATE users SET city = %s WHERE id = %s", (fake.city(), user_id))
    return user_ids


def change_price(cur: psycopg.Cursor) -> str:
    cur.execute(
        "SELECT id, unit_price FROM products WHERE deleted_at IS NULL ORDER BY random() LIMIT 1"
    )
    product_id, old_price = cur.fetchone()
    factor = Decimal(str(round(random.uniform(0.85, 1.2), 2)))
    new_price = (old_price * factor).quantize(Decimal("0.01"))
    cur.execute("UPDATE products SET unit_price = %s WHERE id = %s", (new_price, product_id))
    return f"{product_id} ({old_price} -> {new_price})"


def insert_users(cur: psycopg.Cursor, fake: Faker) -> list[int]:
    user_ids = []
    for _ in range(N_NEW_USERS):
        # Timestamps come from column defaults (now()), as they would from the real app.
        cur.execute(
            """
            INSERT INTO users (email, first_name, last_name, city, country)
            VALUES (%s, %s, %s, %s, %s) RETURNING id
            """,
            (
                f"{fake.user_name()}.{uuid.uuid4().hex[:6]}@{fake.free_email_domain()}",
                fake.first_name(),
                fake.last_name(),
                fake.city(),
                random.choices(COUNTRIES, COUNTRY_WEIGHTS)[0],
            ),
        )
        user_ids.append(cur.fetchone()[0])
    return user_ids


def insert_orders(cur: psycopg.Cursor, new_user_ids: list[int]) -> list[int]:
    """New orders from a mix of brand-new and existing customers."""
    cur.execute(
        "SELECT id FROM users WHERE deleted_at IS NULL AND NOT (id = ANY(%s)) ORDER BY random() LIMIT %s",
        (new_user_ids, N_NEW_ORDERS),
    )
    customers = (new_user_ids + [row[0] for row in cur.fetchall()])[:N_NEW_ORDERS]

    cur.execute("SELECT id, unit_price FROM products WHERE deleted_at IS NULL")
    products = cur.fetchall()

    order_ids = []
    for user_id in customers:
        lines = [(product_id, price, random.randint(1, 3))
                 for product_id, price in random.sample(products, random.randint(1, 3))]
        total = sum(price * quantity for _, price, quantity in lines)
        status = random.choice(["pending", "paid"])
        campaign_id = random.choice([1, 2, 3]) if random.random() < 0.3 else None

        cur.execute(
            """
            INSERT INTO orders (user_id, campaign_id, status, total_amount)
            VALUES (%s, %s, %s, %s) RETURNING id
            """,
            (user_id, campaign_id, status, total),
        )
        order_id = cur.fetchone()[0]
        cur.executemany(
            "INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES (%s, %s, %s, %s)",
            [(order_id, product_id, quantity, price) for product_id, price, quantity in lines],
        )
        if status == "paid":
            insert_payment(cur, order_id, total)
        order_ids.append(order_id)
    return order_ids


def soft_delete_user(cur: psycopg.Cursor, exclude: list[int]) -> int:
    """Account closure: the row stays, deleted_at is set, and the trigger bumps updated_at."""
    cur.execute(
        """
        UPDATE users SET deleted_at = now()
        WHERE id = (
            SELECT id FROM users
            WHERE deleted_at IS NULL AND NOT (id = ANY(%s))
            ORDER BY random() LIMIT 1
        )
        RETURNING id
        """,
        (exclude,),
    )
    return cur.fetchone()[0]


def hard_delete_cancelled_order(cur: psycopg.Cursor) -> str:
    """A cleanup job purging an old cancelled order. The rows are simply gone, so a
    `WHERE updated_at > watermark` query has nothing to return for them."""
    cur.execute("SELECT id FROM orders WHERE status = 'cancelled' ORDER BY id LIMIT 1")
    row = cur.fetchone()
    if row is None:
        return "none (no cancelled orders left)"
    order_id = row[0]
    cur.execute("DELETE FROM order_items WHERE order_id = %s", (order_id,))
    n_items = cur.rowcount
    cur.execute("DELETE FROM orders WHERE id = %s", (order_id,))
    return f"order {order_id} + {n_items} order_items"


def main() -> None:
    fake = Faker()

    # One transaction, so every change shares the same now() and therefore the same updated_at.
    with connect() as conn, conn.cursor() as cur:
        advanced = advance_orders(cur)
        moved = change_cities(cur, fake)
        price_change = change_price(cur)
        new_users = insert_users(cur, fake)
        new_orders = insert_orders(cur, new_users)
        soft_deleted = soft_delete_user(cur, exclude=moved + new_users)
        hard_deleted = hard_delete_cancelled_order(cur)
        cur.execute("SELECT now()")
        changed_at = cur.fetchone()[0]

    print(f"Changes committed at {changed_at:%Y-%m-%d %H:%M:%S %Z}")
    print(f"  inserted users:        {new_users}")
    print(f"  inserted orders:       {new_orders}")
    print(f"  advanced orders:       {', '.join(advanced) or 'none'}")
    print(f"  users changed city:    {moved}")
    print(f"  product price changed: {price_change}")
    print(f"  soft-deleted user:     {soft_deleted}")
    print(f"  hard-deleted:          {hard_deleted}")


if __name__ == "__main__":
    main()
