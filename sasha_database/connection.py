"""Explicit read-only connections and host write transactions."""

from contextlib import contextmanager

import pymysql

from .config import DatabaseSettings, SCHEMA_VARIABLES, configured_schemas


class DatabaseError(RuntimeError):
    """A database failure safe to report without connection details."""


def connect(schema: str, settings: DatabaseSettings, readonly: bool):
    values = settings.values
    if schema not in [values[name] for name in SCHEMA_VARIABLES.values()]:
        raise ValueError("Schema is outside the configured scope")
    connection = pymysql.connect(
        host=values["NON_PROD_HOST"], port=int(values["NON_PROD_PORT"]),
        user=values["NON_PROD_USER"], password=values["NON_PROD_PASS"], database=schema,
        ssl_disabled=True,
        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10, read_timeout=20, write_timeout=10,
        autocommit=False, local_infile=False,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION time_zone='+00:00'")
            cursor.execute("SET SESSION MAX_EXECUTION_TIME=10000")
            cursor.execute("START TRANSACTION READ ONLY" if readonly else "START TRANSACTION")
    except Exception:
        connection.close()
        raise
    return connection


def connect_readonly(schema: str):
    if schema not in configured_schemas().values():
        raise ValueError("Schema is outside the configured scope")
    try:
        return connect(schema, DatabaseSettings.from_environment(), readonly=True)
    except pymysql.MySQLError:
        raise DatabaseError("Could not connect to the database") from None


@contextmanager
def transaction(settings: DatabaseSettings, readonly: bool = False):
    try:
        connection = connect(settings.values["DEAL_MANAGER_OPERATIONS_SCHEMA"], settings, readonly)
        try:
            yield connection
            if not readonly:
                connection.commit()
        finally:
            try:
                connection.rollback()
            finally:
                connection.close()
    except pymysql.MySQLError:
        raise DatabaseError("Database operation failed; the run was not completed") from None
