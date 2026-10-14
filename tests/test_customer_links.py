import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock, patch

from openai_managed import customer_links
from openai_managed.customer_link_database import CustomerLinkDatabase, load_customer_link_database
from openai_managed.sandbox import DockerSandbox, SandboxConfig
from openai_managed.task import SashaTask
from deal_manager_agent.deal_queries import load_database_helper


DATABASE_ENVIRONMENT = {
    "NON_PROD_HOST": "database.example",
    "NON_PROD_PORT": "3306",
    "NON_PROD_USER": "database-user",
    "NON_PROD_PASS": "database-secret",
    "NON_PROD_SSL_CA": "/opt/rds-ca.pem",
    "DEAL_MANAGER_MAIN_SCHEMA": "configured_main",
    "DEAL_MANAGER_OPERATIONS_SCHEMA": "configured_operations",
    "DEAL_MANAGER_CRM_SCHEMA": "configured_crm",
}


class CustomerLinkTests(unittest.TestCase):
    def setUp(self):
        self.database = MagicMock()
        self.database.configured_schemas.return_value = {"main": "configured_main"}
        self.database.quote_schema.return_value = "`configured_main`"
        self.context = {"deal_id": "100", "hq_base_url": "https://hq.example/"}

    def query_rows(self, customer):
        self.database.read_query.side_effect = [{"rows": [{"client_id": 7}]}, {"rows": customer}]

    def test_authentication_link_encodes_full_value_and_uses_bound_ids(self):
        self.query_rows([{"password": "$hash+/=?&."}])
        result = customer_links.build_customer_link(self.database, self.context, "200")
        self.assertEqual(result, {
            "proof_id": 200,
            "url": "https://hq.example/design/200?login=%24hash%2B%2F%3D%3F%26%2E",
            "login_included": True,
        })
        queries = self.database.read_query.call_args_list
        self.assertEqual(queries[0].args[2], (100, 200))
        self.assertIn("`configured_main`.deal_proofs", queries[0].args[1])
        self.assertIn("d.deleted_at IS NULL AND dp.deleted_at IS NULL", queries[0].args[1])
        self.assertEqual(queries[1].args[2], (7,))
        self.assertIn("deleted_at IS NULL", queries[1].args[1])
        self.database.connect.return_value.rollback.assert_called_once()
        self.database.connect.return_value.close.assert_called_once()

    def test_missing_login_returns_plain_link_only_for_an_existing_customer(self):
        for value in (None, ""):
            with self.subTest(value=value):
                self.query_rows([{"password": value}])
                result = customer_links.build_customer_link(self.database, self.context, 200)
                self.assertEqual(result["url"], "https://hq.example/design/200")
                self.assertFalse(result["login_included"])
        self.query_rows([])
        with self.assertRaises(customer_links.CustomerLinkError) as error:
            customer_links.build_customer_link(self.database, self.context, 200)
        self.assertEqual(error.exception.code, "customer_not_found")

    def test_unrelated_or_deleted_relationship_does_not_read_customer(self):
        self.database.read_query.return_value = {"rows": []}
        with self.assertRaises(customer_links.CustomerLinkError) as error:
            customer_links.build_customer_link(self.database, self.context, 200)
        self.assertEqual(error.exception.code, "proof_not_linked")
        self.assertEqual(self.database.read_query.call_count, 1)
        self.database.connect.return_value.close.assert_called_once()

    def test_no_cache_after_proof_creation_or_customer_login_change(self):
        self.database.read_query.side_effect = [
            {"rows": []},
            {"rows": [{"client_id": 7}]}, {"rows": [{"password": "first"}]},
            {"rows": [{"client_id": 7}]}, {"rows": [{"password": "second"}]},
        ]
        with self.assertRaises(customer_links.CustomerLinkError):
            customer_links.build_customer_link(self.database, self.context, 200)
        first = customer_links.build_customer_link(self.database, self.context, 200)
        second = customer_links.build_customer_link(self.database, self.context, 200)
        self.assertTrue(first["url"].endswith("login=first"))
        self.assertTrue(second["url"].endswith("login=second"))
        self.assertEqual(self.database.connect.call_count, 3)

    def test_invalid_ids_and_hq_destinations_do_not_connect(self):
        for value in (None, True, 0, -1, "1.2", "200 OR 1=1", "１２"):
            with self.subTest(value=value), self.assertRaises(customer_links.CustomerLinkError):
                customer_links.build_customer_link(self.database, self.context, value)
        for base in ("", "/hq", "javascript:foo", "https://user:secret@hq.example", "https://hq.example?", "https://hq.example#", "https://hq.example:bad"):
            with self.subTest(base=base), self.assertRaises(customer_links.CustomerLinkError):
                customer_links.build_customer_link(self.database, {**self.context, "hq_base_url": base}, 200)
        self.database.connect.assert_not_called()

    def test_database_errors_close_connection_and_never_become_plain_links(self):
        self.database.read_query.side_effect = RuntimeError("private database detail")
        with self.assertRaises(RuntimeError):
            customer_links.build_customer_link(self.database, self.context, 200)
        self.database.connect.return_value.close.assert_called_once()
        with patch.object(customer_links, "run", side_effect=RuntimeError("private database detail")), \
                patch("sys.argv", ["customer_links.py", "200"]), \
                contextlib.redirect_stdout(io.StringIO()) as output, self.assertRaises(SystemExit):
            customer_links.main()
        result = json.loads(output.getvalue())
        self.assertEqual(result["error_code"], "database_error")
        self.assertNotIn("url", result)
        self.assertNotIn("private", output.getvalue())

    def test_unconfigured_database_fails_without_prompting_for_password(self):
        for name in DATABASE_ENVIRONMENT:
            environment = {**DATABASE_ENVIRONMENT, name: ""}
            with self.subTest(missing=name), patch.dict(os.environ, environment, clear=True):
                with self.assertRaises(customer_links.CustomerLinkError) as error:
                    customer_links.run(200)
                self.assertEqual(error.exception.code, "database_configuration_error")

    def test_helper_uses_environment_configuration_without_database_json(self):
        database = load_database_helper()
        with tempfile.TemporaryDirectory() as directory:
            context_path = Path(directory) / "context.json"
            context_path.write_text(json.dumps(self.context))
            with patch.dict(os.environ, DATABASE_ENVIRONMENT, clear=True), \
                    patch.dict("sys.modules", {"database": database}), \
                    patch.object(customer_links, "CONTEXT_PATH", context_path), \
                    patch.object(database, "connect") as connect, \
                    patch.object(database, "read_query", side_effect=[
                        {"rows": [{"client_id": 7}]}, {"rows": [{"password": "login."}]},
                    ]) as query:
                result = customer_links.run(200)
            self.assertEqual(result["url"], "https://hq.example/design/200?login=login%2E")
            connect.assert_called_once_with("configured_main")
            self.assertIn("`configured_main`.deals", query.call_args_list[0].args[1])


class CustomerLinkRuntimeTests(unittest.TestCase):
    def test_incomplete_configuration_disables_only_database_access(self):
        with patch("openai_managed.customer_link_database.required", side_effect=ValueError("missing")):
            self.assertIsNone(load_customer_link_database())

    def test_container_scopes_deal_and_keeps_secrets_out_of_arguments_and_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            certificate = root / "ca.pem"
            certificate.write_text("synthetic certificate")
            settings = CustomerLinkDatabase(
                {name: value for name, value in DATABASE_ENVIRONMENT.items() if name != "NON_PROD_SSL_CA"},
                certificate,
            )
            sandbox = DockerSandbox(SandboxConfig("image", root, "https://hq.example"), "executor")
            handle = sandbox.prepare(SashaTask("100", "task", "https://crm.example/deal?id=100"))
            with patch.object(sandbox, "_load_customer_link_database", return_value=settings), \
                    patch.dict(os.environ, {"UNRELATED_SECRET": "unrelated", "OPENAI_API_KEY": "openai-secret"}), \
                    patch("openai_managed.sandbox.subprocess.run", return_value=CompletedProcess([], 0)) as run:
                sandbox.start_container()
            run.assert_called_once()
            arguments = run.call_args.args[0]
            command = " ".join(arguments)
            self.assertIn("dst=/opt/sasha/customer_links.py,readonly", command)
            self.assertIn("dst=/opt/sasha/customer_link_context.json,readonly", command)
            self.assertNotIn("/run/secrets", command)
            self.assertNotIn("--env-file", arguments)
            forwarded_names = {arguments[index + 1] for index, value in enumerate(arguments) if value == "--env"}
            self.assertEqual(forwarded_names, set(DATABASE_ENVIRONMENT))
            self.assertIn("dst=/opt/rds-ca.pem,readonly", command)
            self.assertEqual(json.loads((handle.run_directory / "customer_link_context.json").read_text()),
                             {"deal_id": "100", "hq_base_url": "https://hq.example"})
            for name, value in DATABASE_ENVIRONMENT.items():
                self.assertEqual(run.call_args.kwargs["env"][name], value)
            self.assertNotIn("input", run.call_args.kwargs)
            for call in run.call_args_list:
                self.assertNotIn("database-secret", str(call.args))
            for path in handle.run_directory.rglob("*"):
                if path.is_file():
                    self.assertNotIn("database-secret", path.read_text())

    def test_missing_database_does_not_stop_container_or_connect(self):
        with tempfile.TemporaryDirectory() as directory:
            sandbox = DockerSandbox(SandboxConfig("image", Path(directory), "https://hq.example"), "executor")
            sandbox.prepare(SashaTask("100", "task", "https://crm.example/deal?id=100"))
            with patch.object(sandbox, "_load_customer_link_database", return_value=None), \
                    patch("openai_managed.sandbox.subprocess.run") as run:
                sandbox.start_container()
            self.assertEqual(run.call_count, 1)
            self.assertNotIn("--env", run.call_args.args[0])
