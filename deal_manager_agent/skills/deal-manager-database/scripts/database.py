"""Bounded, read-only access to the configured deal manager schemas."""

import argparse
import getpass
import json
import os
from pathlib import Path
import re
import ssl

import pymysql
from dotenv import load_dotenv

SCHEMA_VARIABLES = {
    "main": "DEAL_MANAGER_MAIN_SCHEMA",
    "operations": "DEAL_MANAGER_OPERATIONS_SCHEMA",
    "crm": "DEAL_MANAGER_CRM_SCHEMA",
}


def load_private_configuration():
    configuration_path = os.environ.get("DEAL_MANAGER_DATABASE_CONFIG")
    if not configuration_path and Path("/run/secrets/database.json").is_file():
        configuration_path = "/run/secrets/database.json"
    if configuration_path:
        configuration = json.loads(Path(configuration_path).read_text())
        names = ("NON_PROD_HOST", "NON_PROD_PORT", "NON_PROD_USER", "NON_PROD_PASS",
                 *SCHEMA_VARIABLES.values())
        for name in names:
            if name in configuration:
                os.environ[name] = configuration[name]
        if not os.environ.get("NON_PROD_SSL_CA") and Path("/opt/rds-ca.pem").is_file():
            os.environ["NON_PROD_SSL_CA"] = "/opt/rds-ca.pem"


def quote_schema(value):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise ValueError("Schema names must contain 1–64 letters, digits, underscores or hyphens")
    return f"`{value}`"


def configured_schemas():
    load_private_configuration()
    schemas = {}
    for role, variable in SCHEMA_VARIABLES.items():
        value = os.environ.get(variable, "").strip()
        if not value:
            raise ValueError(f"Missing required environment variable: {variable}")
        quote_schema(value)
        schemas[role] = value
    return schemas


def connect(schema):
    if schema not in configured_schemas().values():
        raise ValueError("Schema is outside the configured scope")
    password = os.environ.get("NON_PROD_PASS") or getpass.getpass("Database password: ")
    context = ssl.create_default_context(cafile=os.environ.get("NON_PROD_SSL_CA"))
    connection = pymysql.connect(
        host=os.environ["NON_PROD_HOST"],
        port=int(os.environ.get("NON_PROD_PORT", "3306")),
        user=os.environ["NON_PROD_USER"],
        password=password,
        database=schema,
        ssl=context,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
        read_timeout=20,
        write_timeout=10,
        autocommit=False,
        local_infile=False,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION time_zone='+00:00'")
            cursor.execute("SET SESSION MAX_EXECUTION_TIME=10000")
            cursor.execute("START TRANSACTION READ ONLY")
    except Exception:
        connection.close()
        raise
    return connection


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--schema", help="An actual schema name from the configured scope")
    selection.add_argument("--role", choices=SCHEMA_VARIABLES, default="main")
    parser.add_argument("--limit", type=int, default=100)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("schemas", help="Show schema roles and names, without credentials")
    commands.add_parser("tables")
    describe = commands.add_parser("describe")
    describe.add_argument("table")
    query = commands.add_parser("query")
    query.add_argument("sql")
    query.add_argument("--params", default="[]", help="JSON array of bound values")
    args = parser.parse_args()
    if args.env_file:
        load_dotenv(Path(args.env_file), override=False)
    schemas = configured_schemas()
    if args.command == "schemas":
        print(json.dumps(schemas, indent=2))
        return
    schema = args.schema or schemas[args.role]
    match args.command:
        case "tables":
            sql = "SELECT TABLE_NAME, TABLE_TYPE, TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME"
            parameters = (schema,)
        case "describe":
            sql = "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION"
            parameters = (schema, args.table)
        case "query":
            sql = args.sql
            parameters = json.loads(args.params)
            if not isinstance(parameters, list):
                raise ValueError("Parameters must be a JSON array")
    connection = connect(schema)
    try:
        result = read_query(connection, sql, parameters, args.limit)
        print(json.dumps(result, default=str, indent=2))
    finally:
        connection.rollback()
        connection.close()


if __name__ == "__main__":
    main()
