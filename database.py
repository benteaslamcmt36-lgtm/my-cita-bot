import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.getenv("DATABASE_PATH", "clients.db")


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                nie TEXT,
                city TEXT NOT NULL,
                procedure TEXT NOT NULL,
                telegram_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)


def add_client(name, nie, city, procedure, telegram_id=None):
    with get_connection() as conn:
        cursor = conn.execute("""
            INSERT INTO clients
            (name, nie, city, procedure, telegram_id)
            VALUES (?, ?, ?, ?, ?)
        """, (name, nie, city, procedure, telegram_id))
        return cursor.lastrowid


def get_clients():
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM clients ORDER BY id DESC"
        ).fetchall()


def delete_client(client_id):
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM clients WHERE id = ?",
            (client_id,)
        )
        return cursor.rowcount > 0


init_db()
