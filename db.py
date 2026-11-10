"""Database access: one PostgreSQL connection and one transaction per request."""
import os

import psycopg2
from psycopg2.extras import RealDictCursor
from flask import g


def get_db():
    if 'db' not in g:
        g.db = psycopg2.connect(os.environ['DATABASE_URL'], cursor_factory=RealDictCursor)
    return g.db


def query(sql, params=()):
    """Run one SQL statement. Returns the rows, or the affected row count if there are none."""
    with get_db().cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else cur.rowcount


def query_one(sql, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


def close_db(exc=None):
    """Commit when the request succeeded, roll back when it failed."""
    conn = g.pop('db', None)
    if conn is not None:
        if exc is None:
            conn.commit()
        else:
            conn.rollback()
        conn.close()
