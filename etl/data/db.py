import os
import psycopg2
from dotenv import load_dotenv
from pgvector.psycopg2 import register_vector

load_dotenv()

def get_db_connection():
    conn = psycopg2.connect(
        host=os.getenv("PG_HOST", "localhost"),
        port=os.getenv("PG_PORT", "5432"),
        database=os.getenv("PG_DATABASE", "memoryos"),
        user=os.getenv("PG_USER", "memoryos"),
        password=os.getenv("PG_PASSWORD", "memoryos")
    )
    register_vector(conn)  # 开启 pgvector 支持
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
            return []  # 非查询语句
        finally:
            cur.close()
    finally:
        conn.close()