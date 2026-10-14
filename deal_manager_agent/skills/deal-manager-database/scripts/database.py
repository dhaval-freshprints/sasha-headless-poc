"""Command-line access to the shared read-only database layer."""
import argparse
import getpass
import os
import json
from pathlib import Path
import sys
from dotenv import load_dotenv

# The installed skill can also be run directly from a source checkout.
for parent in Path(__file__).resolve().parents:
    if (parent / "sasha_database").is_dir():
        sys.path.insert(0, str(parent))
        break
from sasha_database.config import SCHEMA_VARIABLES, configured_schemas, quote_schema, env_value
from sasha_database.read import connect, read_query

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
    if not env_value("NON_PROD_PASS", "NON_PROD_CRM_PASS", "NON_PROD_V3_PASS"):
        os.environ["NON_PROD_PASS"] = getpass.getpass("Database password: ")
    connection = connect(schema)
    try:
        result = read_query(connection, sql, parameters, args.limit)
        print(json.dumps(result, default=str, indent=2))
    finally:
        connection.rollback()
        connection.close()


if __name__ == "__main__":
    main()
