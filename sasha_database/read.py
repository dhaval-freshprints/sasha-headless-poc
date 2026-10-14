"""Bounded read queries shared by host and agent tools."""
import re
from .config import configured_schemas, quote_schema
from .connection import connect_readonly as connect

def read_query(connection, sql, parameters=(), limit=100):
    if not 1 <= limit <= 1000:
        raise ValueError("Row limit must be between 1 and 1000")
    if not re.match(r"^\s*SELECT\b", sql, re.IGNORECASE):
        raise ValueError("Only SELECT queries are supported")
    forbidden = r";|--|/\*|\b(INTO|OUTFILE|DUMPFILE|SLEEP|BENCHMARK|GET_LOCK|RELEASE_LOCK|LOAD_FILE)\b|\bFOR\s+(UPDATE|SHARE)\b"
    if re.search(forbidden, sql, re.IGNORECASE):
        raise ValueError("Unsupported SQL; use parameters for values")
    with connection.cursor() as cursor:
        bounded_sql = f"SELECT * FROM ({sql}) AS bounded_result LIMIT %s"
        cursor.execute(bounded_sql, (*parameters, limit + 1))
        rows = cursor.fetchmany(limit + 1)
    return {"rows": rows[:limit], "truncated": len(rows) > limit, "row_limit": limit}
