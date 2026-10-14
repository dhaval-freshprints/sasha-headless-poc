"""Create a customer HQ URL for one proof on the runner's current deal."""

import argparse
import json
import os
from pathlib import Path
from urllib.parse import quote, urlsplit


CONTEXT_PATH = Path("/opt/sasha/customer_link_context.json")
from sasha_database import read as database_helper
from sasha_database.config import DATABASE_ENVIRONMENT_VARIABLES


class CustomerLinkError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def positive_id(value) -> int:
    if isinstance(value, bool) or not str(value).isascii() or not str(value).isdigit():
        raise CustomerLinkError("invalid_id", "Deal and proof IDs must be positive integers.")
    number = int(value)
    if number <= 0:
        raise CustomerLinkError("invalid_id", "Deal and proof IDs must be positive integers.")
    return number


def validate_hq_base_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in {"http", "https"} and parsed.hostname
            and not parsed.username and not parsed.password
            and "?" not in value and "#" not in value
            and not any(character.isspace() for character in value)
        )
        parsed.port
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise CustomerLinkError("hq_configuration_error", "A valid FP_HQ_BASE_URL is required.")
    return value.rstrip("/")


def customer_for_proof(database, connection, schema: str, deal_id: int, proof_id: int) -> dict:
    rows = database.read_query(connection, (
        f"SELECT d.client_id FROM {schema}.deals d "
        f"JOIN {schema}.deal_proofs dp ON dp.deal_id=d.id "
        "WHERE d.id=%s AND dp.proof_id=%s "
        "AND d.deleted_at IS NULL AND dp.deleted_at IS NULL"
    ), (deal_id, proof_id), limit=1)["rows"]
    if not rows:
        raise CustomerLinkError("proof_not_linked", "The proof is not linked to the current active deal record.")
    customers = database.read_query(connection, (
        f"SELECT password FROM {schema}.user WHERE id=%s AND deleted_at IS NULL"
    ), (rows[0]["client_id"],), limit=1)["rows"]
    if not customers:
        raise CustomerLinkError("customer_not_found", "The deal's customer record is unavailable.")
    return customers[0]


def build_customer_link(database, context: dict, proof_id) -> dict:
    deal_id = positive_id(context.get("deal_id"))
    proof_id = positive_id(proof_id)
    base_url = validate_hq_base_url(context.get("hq_base_url", ""))
    schema_name = database.configured_schemas()["main"]
    schema = database.quote_schema(schema_name)
    connection = database.connect(schema_name)
    try:
        customer = customer_for_proof(database, connection, schema, deal_id, proof_id)
    finally:
        try:
            connection.rollback()
        finally:
            connection.close()
    password = customer.get("password")
    url = f"{base_url}/design/{proof_id}"
    if password:
        url += "?login=" + quote(password, safe="").replace(".", "%2E")
    return {"proof_id": proof_id, "url": url, "login_included": bool(password)}


def run(proof_id) -> dict:
    if any(not os.environ.get(name) for name in DATABASE_ENVIRONMENT_VARIABLES):
        raise CustomerLinkError("database_configuration_error", "Customer link database access is not configured.")
    try:
        context = json.loads(CONTEXT_PATH.read_text())
    except (OSError, ValueError):
        raise CustomerLinkError("task_configuration_error", "Customer link task context is unavailable.") from None
    return build_customer_link(database_helper, context, proof_id)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("proof_id")
    args = parser.parse_args()
    try:
        result = run(args.proof_id)
    except CustomerLinkError as error:
        result = {"error_code": error.code, "error_message": str(error)}
    except Exception:
        # Driver exceptions can include connection details. Never print them to agent logs.
        result = {"error_code": "database_error", "error_message": "Could not read customer link data. No link was generated."}
    print(json.dumps(result))
    if "error_code" in result:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
