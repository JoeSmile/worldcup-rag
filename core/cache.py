"""Backward-compatible alias; use core.query_cache.get_query_cache()."""

from core.query_cache import get_query_cache

get_cache = get_query_cache

__all__ = ["get_cache", "get_query_cache"]
