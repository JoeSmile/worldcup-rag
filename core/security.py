"""Security filter: input sanitization, SQL safety checks, output scanning.

Designed for World Cup RAG — redacts credentials/PII patterns but does NOT strip
player or team names (answers are expected to mention public figures).

Outbound stack: regex (L1) + optional high-entropy token scan (L2). No Presidio.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

import sqlparse

from core.logger import get_logger, log_extra
from core.security_config import get_security_config

logger = get_logger("security")

# Dangerous SQL tokens (agent-generated SQL only — not user natural language)
_UNSAFE_SQL_TOKENS = re.compile(
    r"\b(DROP|DELETE|UPDATE|INSERT|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|EXEC|EXECUTE)\b",
    re.IGNORECASE,
)

# Obvious injection in user text (stacked statements / DDL), not bare keywords in prose
_USER_SQL_INJECTION = re.compile(
    r"(;\s*(drop|delete|truncate|alter|create|insert|update)\b|"
    r"\bunion\s+(all\s+)?select\b|"
    r"'\s*or\s+'1'\s*=\s*'1)",
    re.IGNORECASE,
)

_SENSITIVE_PATTERNS: dict[str, re.Pattern[str]] = {
    "api_key": re.compile(r"sk-(?:proj-)?[a-zA-Z0-9_-]{20,}"),
    "jwt": re.compile(r"eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}"),
    "phone_cn": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "id_card": re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
    "email": re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
}

_UNION_SQL = re.compile(r"\bUNION\b", re.IGNORECASE)


@dataclass
class ScanResult:
    safe: bool
    issues: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"safe": self.safe, "issues": self.issues}


def _shannon_entropy(token: str) -> float:
    if not token:
        return 0.0
    counts: dict[str, int] = {}
    for char in token:
        counts[char] = counts.get(char, 0) + 1
    length = len(token)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def _entropy_token_pattern(min_length: int) -> re.Pattern[str]:
    return re.compile(rf"[A-Za-z0-9_\-+/=]{{{min_length},}}")


def _normalize_issues(issues: list[dict[str, str]]) -> list[dict[str, str]]:
    """Merge duplicate issue types; aggregate high_entropy_secret count."""
    merged: dict[str, dict[str, str]] = {}
    for issue in issues:
        issue_type = issue.get("type") or "unknown"
        if issue_type == "high_entropy_secret":
            if issue_type in merged:
                merged[issue_type]["count"] = str(int(merged[issue_type].get("count", "1")) + 1)
            else:
                merged[issue_type] = {"type": issue_type, "severity": issue.get("severity", "high"), "count": "1"}
        elif issue_type not in merged:
            merged[issue_type] = dict(issue)
    return list(merged.values())


class SecurityFilter:
    """Input sanitization, SQL validation, and output scanning."""

    @staticmethod
    def _cfg() -> Any:
        return get_security_config().security

    @staticmethod
    def _scan_entropy_tokens(text: str) -> list[dict[str, str]]:
        cfg = SecurityFilter._cfg()
        if not cfg.entropy_scan_enabled:
            return []

        issues: list[dict[str, str]] = []
        pattern = _entropy_token_pattern(cfg.entropy_min_token_length)
        entropy_hits = 0
        for match in pattern.finditer(text):
            token = match.group(0)
            if _shannon_entropy(token) >= cfg.entropy_threshold:
                entropy_hits += 1
        if entropy_hits:
            issues.append(
                {
                    "type": "high_entropy_secret",
                    "severity": "high",
                    "count": str(entropy_hits),
                }
            )
        return issues

    @staticmethod
    def _redact_entropy_tokens(text: str) -> str:
        cfg = SecurityFilter._cfg()
        if not cfg.entropy_scan_enabled:
            return text

        placeholder = cfg.redact_placeholder
        pattern = _entropy_token_pattern(cfg.entropy_min_token_length)

        def _replace(match: re.Match[str]) -> str:
            token = match.group(0)
            if _shannon_entropy(token) >= cfg.entropy_threshold:
                return placeholder
            return token

        return pattern.sub(_replace, text)

    @staticmethod
    def sanitize_text(text: str) -> str:
        """Replace known sensitive patterns (API keys, phone, email, etc.)."""
        if not text:
            return text
        for pattern in _SENSITIVE_PATTERNS.values():
            text = pattern.sub(SecurityFilter._cfg().redact_placeholder, text)
        return SecurityFilter._redact_entropy_tokens(text)

    @staticmethod
    def sanitize_chat_payload(data: dict[str, Any]) -> dict[str, Any]:
        """Sanitize query and optional history fields in a chat request body."""
        if "query" in data and isinstance(data["query"], str):
            data["query"] = SecurityFilter.sanitize_text(data["query"])

        history = data.get("history")
        if isinstance(history, list):
            for item in history:
                if not isinstance(item, dict):
                    continue
                for key in ("user", "assistant"):
                    if isinstance(item.get(key), str):
                        item[key] = SecurityFilter.sanitize_text(item[key])
        return data

    @staticmethod
    def looks_like_sql_injection_in_query(text: str) -> bool:
        """Detect obvious SQL injection in user natural language (not keyword substring checks)."""
        if not text or not text.strip():
            return False
        return _USER_SQL_INJECTION.search(text) is not None

    @staticmethod
    def is_unsafe_sql(sql: str) -> bool:
        """Return True when SQL must be rejected (non-SELECT, DDL, or stacked statements)."""
        normalized = (sql or "").strip()
        if not normalized:
            return True
        if not normalized.upper().startswith("SELECT"):
            return True

        statements = [stmt for stmt in sqlparse.split(normalized) if stmt.strip()]
        if not statements:
            return True
        if len(statements) > 1:
            return True

        for stmt in statements:
            if _UNSAFE_SQL_TOKENS.search(stmt):
                return True
            if _UNION_SQL.search(stmt):
                return True
        return False

    @staticmethod
    def scan_output(text: str) -> ScanResult:
        """Scan text for leaked secrets / contact PII (not public figure names)."""
        issues: list[dict[str, str]] = []
        if not text:
            return ScanResult(safe=True, issues=issues)

        for name, pattern in _SENSITIVE_PATTERNS.items():
            if pattern.search(text):
                issues.append({"type": name, "severity": "high"})

        issues.extend(SecurityFilter._scan_entropy_tokens(text))
        return ScanResult(safe=len(issues) == 0, issues=_normalize_issues(issues))

    @staticmethod
    def scan_and_redact_text(text: str) -> tuple[str, ScanResult]:
        """Scan text; redact sensitive spans when configured."""
        scan = SecurityFilter.scan_output(text)
        if scan.safe:
            return text, scan
        if SecurityFilter._cfg().redact_output_on_issue:
            return SecurityFilter.sanitize_text(text), scan
        return text, scan

    @staticmethod
    def scan_and_redact_chat_response(payload: dict[str, Any]) -> tuple[dict[str, Any], ScanResult]:
        """Outbound scan for chat JSON: answer (+ optional sql_generated)."""
        merged_issues: list[dict[str, str]] = []
        cfg = SecurityFilter._cfg()

        answer = payload.get("answer")
        if isinstance(answer, str):
            new_answer, scan = SecurityFilter.scan_and_redact_text(answer)
            payload["answer"] = new_answer
            merged_issues.extend(scan.issues)

        if cfg.scan_sql_generated:
            sql = payload.get("sql_generated")
            if isinstance(sql, str):
                new_sql, scan = SecurityFilter.scan_and_redact_text(sql)
                payload["sql_generated"] = new_sql
                merged_issues.extend(scan.issues)

        return payload, ScanResult(safe=len(merged_issues) == 0, issues=_normalize_issues(merged_issues))

    @staticmethod
    def sanitize_deep(value: Any) -> Any:
        """Recursively sanitize strings in log / trace payloads."""
        if not SecurityFilter._cfg().enabled:
            return value
        if isinstance(value, str):
            return SecurityFilter.sanitize_text(value)
        if isinstance(value, dict):
            return {key: SecurityFilter.sanitize_deep(item) for key, item in value.items()}
        if isinstance(value, list):
            return [SecurityFilter.sanitize_deep(item) for item in value]
        if isinstance(value, tuple):
            return tuple(SecurityFilter.sanitize_deep(item) for item in value)
        return value

    @staticmethod
    def sanitize_langsmith_trace(payload: dict[str, Any]) -> dict[str, Any]:
        """LangSmith Client anonymizer hook for run inputs/outputs."""
        if not isinstance(payload, dict):
            return payload
        sanitized = SecurityFilter.sanitize_deep(payload)
        return sanitized if isinstance(sanitized, dict) else payload

    @staticmethod
    def redact_chat_result(result: dict[str, Any]) -> dict[str, Any]:
        """Outbound redaction for agent/cache paths (same rules as HTTP middleware)."""
        cfg = SecurityFilter._cfg()
        if not result or not cfg.enabled or not cfg.scan_output:
            return result
        redacted, _ = SecurityFilter.scan_and_redact_chat_response(result)
        return redacted

    @staticmethod
    def safe_error_detail(exc: BaseException | str, *, max_length: int = 240) -> str:
        """Sanitized, length-capped error text for HTTP responses."""
        default = "An internal error occurred"
        if not SecurityFilter._cfg().enabled:
            return default
        sanitized = SecurityFilter.sanitize_text(str(exc)).strip()
        return sanitized[:max_length] if sanitized else default

    @staticmethod
    def audit(
        direction: str,
        event: str,
        *,
        trace_id: str | None = None,
        **context: Any,
    ) -> None:
        if not SecurityFilter._cfg().audit_log:
            return
        logger.info(
            "security audit",
            extra=log_extra(direction=direction, event=event, trace_id=trace_id, **context),
        )
