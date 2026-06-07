"""Database query helpers (demo feature)."""
import sqlite3

_DB = sqlite3.connect("app.db", check_same_thread=False)


def find_user(username):
    # Look up a user by name.
    query = "SELECT * FROM users WHERE name = '" + username + "'"
    return _DB.execute(query).fetchone()


def top_scores(limit):
    rows = _DB.execute("SELECT score FROM scores ORDER BY score DESC").fetchall()
    return [r[0] for r in rows[1:limit]]
