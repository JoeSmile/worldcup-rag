import hashlib
import json
import math
import re
import sys
from functools import lru_cache
from pathlib import Path

from langchain_openai import OpenAIEmbeddings

from core.config import settings
from core.logger import get_logger, log_extra

logger = get_logger("tools")

DATA_DIR = Path(__file__).resolve().parent / "etl" / "data"
if str(DATA_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_DIR))

from db import execute_query  # noqa: E402

EMBEDDING_DIMENSIONS = settings.embedding_dimensions
DEFAULT_COLLECTION_PATTERN = "worldcup-%"
PLAYER_ALIASES_PATH = DATA_DIR / "player_aliases.json"
PLAYER_CAREERS_COLLECTION = "worldcup-player_careers"


@lru_cache(maxsize=1)
def _load_player_aliases() -> list[dict]:
    if not PLAYER_ALIASES_PATH.is_file():
        return []
    with open(PLAYER_ALIASES_PATH, encoding="utf-8") as file:
        return json.load(file)


def resolve_player_id(query: str) -> str | None:
    """Match Chinese nicknames / aliases to canonical player_id (P-xxxxx)."""
    normalized = query.strip()
    if not normalized:
        return None

    lowered = normalized.lower()
    scored: list[tuple[int, str]] = []

    for entry in _load_player_aliases():
        player_id = entry["player_id"]
        score = 0
        for alias in entry.get("aliases", []):
            alias_text = alias.strip()
            if not alias_text:
                continue
            alias_lower = alias_text.lower()
            if lowered == alias_lower or normalized == alias_text:
                score = max(score, 100 + len(alias_text))
            elif alias_lower in lowered or alias_text in normalized:
                score = max(score, len(alias_text))

        for hint in entry.get("disambiguate", []):
            hint_text = hint.strip()
            if hint_text and (hint_text.lower() in lowered or hint_text in normalized):
                score += 20

        if score > 0:
            scored.append((score, player_id))

    if not scored:
        return None

    scored.sort(key=lambda item: (-item[0], item[1]))
    best_score = scored[0][0]
    top_ids = [player_id for points, player_id in scored if points == best_score]
    return top_ids[0] if len(top_ids) == 1 else None


def fetch_player_career_by_id(player_id: str) -> dict | None:
    """Fetch a single player career fact card by player_id."""
    external_id = f"player_career:{player_id}"
    rows = execute_query(
        """
        SELECT d.collection, d.external_id, d.entity_type, dc.content
        FROM documents d
        JOIN document_chunks dc ON dc.document_id = d.id
        WHERE d.collection = %s AND d.external_id = %s
        LIMIT 1
        """,
        (PLAYER_CAREERS_COLLECTION, external_id),
    )
    if not rows:
        return None
    return {
        "collection": rows[0][0],
        "external_id": rows[0][1],
        "entity_type": rows[0][2],
        "content": rows[0][3],
        "similarity": 1.0,
        "resolved_via": "alias",
    }


def _merge_player_results(primary: dict, others: list[dict], limit: int) -> list[dict]:
    results = [primary]
    seen = {primary["external_id"]}
    for row in others:
        if row["external_id"] in seen:
            continue
        results.append(row)
        seen.add(row["external_id"])
        if len(results) >= limit:
            break
    return results


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
    api_key = settings.llm_api_key
    if not api_key:
        return None

    kwargs: dict[str, object] = {
        "model": settings.embedding_model,
        "dimensions": settings.embedding_dimensions,
        "api_key": api_key,
        "check_embedding_ctx_length": False,
        "base_url": settings.resolved_embedding_base_url,
    }
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
    player_id = resolve_player_id(name)
    if player_id:
        primary = fetch_player_career_by_id(player_id)
        if primary:
            if limit <= 1:
                return [primary]
            extra = semantic_search(name, collection=None, limit=limit)
            return _merge_player_results(primary, extra, limit)
    return semantic_search(name, collection=None, limit=limit)


def search_by_vector(query_text: str, limit: int = 5) -> list[dict]:
    """Backward-compatible wrapper for semantic World Cup fact-card search."""
    return semantic_search(query_text, limit=limit)


def get_player_stats(player_name: str, limit: int = 5) -> list[dict]:
    """Search player career/stat fact cards by player name or Chinese nickname."""
    player_id = resolve_player_id(player_name)
    if player_id:
        primary = fetch_player_career_by_id(player_id)
        if primary:
            if limit <= 1:
                return [primary]
            extra = semantic_search(player_name, collection=PLAYER_CAREERS_COLLECTION, limit=limit)
            return _merge_player_results(primary, extra, limit)

    return semantic_search(player_name, collection=PLAYER_CAREERS_COLLECTION, limit=limit)


_FORBIDDEN_TABLES = (
    "players",
    "player_careers",
    "matches",
    "goals",
    "tournaments",
    "bookings",
    "world_cup_player_stats",
    "worldcup_awards",
    "women_world_cup_goals",
)


def _find_forbidden_table(sql: str) -> str | None:
    lowered = sql.lower()
    for name in _FORBIDDEN_TABLES:
        if re.search(rf"\b(from|join)\s+{re.escape(name)}\b", lowered):
            return name
    return None


def execute_sql(sql: str):
    """Execute read-only SQL; return rows or a structured error for the agent."""
    normalized = sql.strip()
    if not normalized.upper().startswith("SELECT"):
        return {"error": "Only SELECT queries are allowed", "sql": sql}

    forbidden = _find_forbidden_table(normalized)
    if forbidden:
        logger.warning(
            "sql rejected forbidden table",
            extra=log_extra(table=forbidden, sql_preview=normalized[:120]),
        )
        return {
            "error": (
                f"Table '{forbidden}' does not exist. "
                "Use vw_player_summary, vw_match_summary, vw_team_tournament_summary, "
                "or documents/document_chunks instead."
            ),
            "sql": sql,
        }

    try:
        rows = execute_query(sql)
        return {"rows": rows, "row_count": len(rows)}
    except Exception as exc:
        message = str(exc).strip().split("\n")[0]
        logger.warning(
            "sql execution failed",
            extra=log_extra(error=message, sql_preview=normalized[:120]),
        )
        return {"error": message, "sql": sql}


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