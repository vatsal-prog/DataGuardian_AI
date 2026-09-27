"""Read-only SQL checks shared by the query agent and database sources."""

from __future__ import annotations

import re

from dataguardian.pipeline_error import PipelineError

_FORBIDDEN = re.compile(
    r"(?is)\b(insert|update|delete|drop|alter|create|attach|detach|pragma|"
    r"grant|revoke|copy|call|execute|into|truncate|merge|replace)\b"
)


def assert_read_only(sql: str) -> str:
    """Return a single SELECT statement, or raise if the SQL is not read-only."""
    statement = (sql or "").strip()
    if not statement:
        raise PipelineError("A SQL statement is required.")
    if statement.endswith(";"):
        statement = statement[:-1].strip()
    if ";" in statement:
        raise PipelineError("Only a single SQL statement is allowed.")
    if not re.match(r"(?is)^select\b", statement):
        raise PipelineError("Only SELECT statements are allowed.")
    if _FORBIDDEN.search(statement):
        raise PipelineError("That statement contains a keyword that is not allowed.")
    return statement


def quote_ident(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""):
        raise PipelineError(f"Unsafe identifier: {name!r}")
    return f'"{name}"'
