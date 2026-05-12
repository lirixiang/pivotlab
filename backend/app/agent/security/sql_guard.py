"""SQL guard: parse and reject unsafe statements for the read-only DB tool."""
from __future__ import annotations

import sqlglot
from sqlglot import exp

ALLOWED = {exp.Select, exp.With, exp.Union, exp.Subquery}
FORBIDDEN_KEYWORDS = ("insert", "update", "delete", "drop", "alter", "truncate", "create", "grant", "revoke", "copy", "vacuum")


class UnsafeSQLError(ValueError):
    pass


def assert_readonly(sql: str) -> None:
    lowered = sql.lower()
    if ";" in sql.rstrip(";"):
        raise UnsafeSQLError("Multiple statements are not allowed")
    for kw in FORBIDDEN_KEYWORDS:
        if f" {kw} " in f" {lowered} " or lowered.lstrip().startswith(kw):
            raise UnsafeSQLError(f"Forbidden keyword: {kw}")
    try:
        parsed = sqlglot.parse_one(sql, read="postgres")
    except Exception as e:  # noqa: BLE001
        raise UnsafeSQLError(f"SQL parse error: {e}") from e
    if not isinstance(parsed, tuple(ALLOWED)):
        raise UnsafeSQLError(f"Only SELECT/WITH/UNION queries are allowed, got {type(parsed).__name__}")
