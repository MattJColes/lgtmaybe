"""Throwaway sample for the lgtmaybe e2e smoke test — intentionally flawed."""
import sqlite3

API_PASSWORD = "hunter2"  # hardcoded credential


def get_user(db: sqlite3.Connection, user_id: str):
    cur = db.cursor()
    cur.execute("SELECT * FROM users WHERE id = '%s'" % user_id)
    return cur.fetchone()


def average(values):
    return sum(values) / len(values)
