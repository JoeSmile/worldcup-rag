import hashlib
import math
import os
import sys
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings

DATA_DIR = Path(__file__).resolve().parent / "etl" / "data"
if str(DATA_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_DIR))

from db import execute_query  # noqa: E402

load_dotenv()

EMBEDDING_DIMENSIONS = int(os.getenv("EMBEDDING_DIMENSIONS", "1024"))
DEFAULT_COLLECTION_PATTERN = "worldcup-%"


def _mock_embedding(text: str, dimensions: int) -> list[float]:
    """Deterministic fallback compatible with memoryOS mock embeddings."""
    seed = hashlib.sha256(text.encode("utf-8")).digest()
    values: list[float] = []
    counter = 0
    while len(values) < dimensions:
        block = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
        for index in range(0, len(block) - 3, 4):
            if len(values) >= dimensions:
                break
            number = int.from_bytes(block[index : index + 4], "big")
            values.append((number / 2**32) * 2 - 1)
        counter += 1

    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0:
        return values
    return [value / norm for value in values]


@lru_cache(maxsize=1)
def _embedding_client() -> OpenAIEmbeddings | None:
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("API_KEY")
    if not api_key:
        return None

    base_url = (
        os.getenv("EMBEDDING_BASE_URL")
        or os.getenv("OPENAI_BASE_URL")
        or os.getenv("API_BASE")
    )
    kwargs: dict[str, object] = {
        "model": os.getenv("EMBEDDING_MODEL", "text-embedding-v4"),
        "dimensions": EMBEDDING_DIMENSIONS,
        "api_key": api_key,
        "check_embedding_ctx_length": False,
    }
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAIEmbeddings(**kwargs)


def embed_query(query: str) -> list[float]:
    """Create a 1024-dim query embedding aligned with the copied pgVector data."""
    client = _embedding_client()
    if client is None:
        return _mock_embedding(query, EMBEDDING_DIMENSIONS)
    return client.embed_query(query)


def semantic_search(
    query: str,
    collection: str | None = None,
    limit: int = 5,
) -> list[dict]:
    """Search World Cup fact-card chunks from documents/document_chunks."""
    safe_limit = max(1, min(int(limit), 20))
    query_vec = embed_query(query)

    if collection:
        where_clause = "d.collection = %s"
        params = (query_vec, collection, query_vec, safe_limit)
    else:
        where_clause = "d.collection LIKE %s"
        params = (query_vec, DEFAULT_COLLECTION_PATTERN, query_vec, safe_limit)

    sql = f"""
        SELECT
            d.collection,
            d.external_id,
            d.entity_type,
            dc.content,
            1 - (dc.embedding <=> %s::vector) AS similarity
        FROM document_chunks dc
        JOIN documents d ON d.id = dc.document_id
        WHERE {where_clause}
        ORDER BY dc.embedding <=> %s::vector
        LIMIT %s
    """
    rows = execute_query(sql, params)
    return [
        {
            "collection": row[0],
            "external_id": row[1],
            "entity_type": row[2],
            "content": row[3],
            "similarity": float(row[4]),
        }
        for row in rows
    ]


def search_players_by_name(name: str, limit: int = 5) -> list[dict]:
    """Search player-related World Cup fact cards."""
    return semantic_search(name, collection=None, limit=limit)


def search_by_vector(query_text: str, limit: int = 5) -> list[dict]:
    """Backward-compatible wrapper for semantic World Cup fact-card search."""
    return semantic_search(query_text, limit=limit)


def get_player_stats(player_name: str, limit: int = 5) -> list[dict]:
    """Search player career/stat fact cards by player name."""
    return semantic_search(player_name, collection="worldcup-player_careers", limit=limit)


def execute_sql(sql: str):
    """Execute read-only SQL for debugging and analysis."""
    if not sql.strip().upper().startswith("SELECT"):
        raise ValueError("Only SELECT queries are allowed")
    return execute_query(sql)


tools = [
    {
        "name": "semantic_search",
        "description": "Search World Cup fact-card chunks by semantic meaning.",
        "parameters": {"query": {"type": "string"}},
    },
    {
        "name": "search_players_by_name",
        "description": "Search player-related World Cup fact cards.",
        "parameters": {"name": {"type": "string"}},
    },
    {
        "name": "get_player_stats",
        "description": "Search player career/stat fact cards.",
        "parameters": {"player_name": {"type": "string"}},
    },
    {
        "name": "execute_sql",
        "description": "Execute read-only SQL queries against the copied pgVector database.",
        "parameters": {"sql": {"type": "string"}},
    },
]