import sys
from pathlib import Path

import psycopg2
from pgvector.psycopg2 import register_vector

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.config import settings  # noqa: E402


def get_db_connection():
    conn = psycopg2.connect(**settings.pg_connection_kwargs())
    register_vector(conn)
    return conn


def execute_query(sql: str, params: tuple | None = None):
    """执行 SQL 并返回结果（用于 Agent 工具）"""
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        if params is None:
            cur.execute(sql)
        else:
            cur.execute(sql, params)
        try:
            return cur.fetchall()
        except psycopg2.ProgrammingError:
            return []
        finally:
            cur.close()
    finally:
        conn.close()
