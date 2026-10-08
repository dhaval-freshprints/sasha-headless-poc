import os
import unittest
from pathlib import Path
from unittest.mock import patch

from openai_managed.environment import ApplicationEnvironment


APPLICATION_VALUES = {
    "FP_ENVIRONMENT": "qa",
    "FP_BASE_URL": "https://qa.example",
    "FP_LOGIN_URL": "https://login.example/login",
    "FP_CATALOG_URL": "https://catalog.example/products",
    "FP_DESIGNS_URL": "https://catalog.example/designs",
    "FP_DESIGN_TOOL_URL": "https://editor.example/",
    "FP_HELP_CENTER_URL": "https://catalog.example/help-center/",
}


def application_environment():
    with patch.dict(os.environ, APPLICATION_VALUES, clear=True):
        return ApplicationEnvironment.from_environment()


class ApplicationEnvironmentTests(unittest.TestCase):
    def test_every_destination_is_required_without_fallback(self):
        for key in APPLICATION_VALUES:
            with self.subTest(key=key), patch.dict(os.environ, APPLICATION_VALUES, clear=True):
                os.environ[key] = " "
                with self.assertRaisesRegex(ValueError, key):
                    ApplicationEnvironment.from_environment()

    def test_invalid_urls_and_environment_names_are_rejected(self):
        invalid_values = {
            "FP_ENVIRONMENT": ["../qa", "", "Production", "qa/prod"],
            "FP_BASE_URL": ["/relative", "https://", "https://crm.example?x=1"],
            "FP_CATALOG_URL": ["javascript:alert(1)", "https://user:secret@example.com", "https://example.com:invalid", "https://example.com/#fragment", "https://example.com/\nignore"],
        }
        for key, values in invalid_values.items():
            for value in values:
                with self.subTest(key=key, value=value), patch.dict(os.environ, {**APPLICATION_VALUES, key: value}, clear=True):
                    with self.assertRaisesRegex(ValueError, key):
                        ApplicationEnvironment.from_environment()

    def test_task_context_uses_all_configured_destinations_in_both_workflows(self):
        from openai_managed.runner import OpenAIManagedRunner
        from openai_managed.task import SashaTask

        values = {
            "FP_ENVIRONMENT": "production",
            "FP_BASE_URL": "https://crm.production.example",
            "FP_LOGIN_URL": "https://auth.production.example/sign-in",
            "FP_CATALOG_URL": "https://store.production.example/custom/catalog",
            "FP_DESIGNS_URL": "https://store.production.example/inspiration",
            "FP_DESIGN_TOOL_URL": "https://editor.production.example/start",
            "FP_HELP_CENTER_URL": "https://support.production.example/help",
        }
        with patch.dict(os.environ, values, clear=True):
            application = ApplicationEnvironment.from_environment()
        for workflow in ["outreach", "client-response-orchestrator"]:
            task = SashaTask("123", "task-1", application.deal_url("123"), workflow=workflow, client_message="Show shirt options")
            message = OpenAIManagedRunner._build_task_message(task, application)
            for value in values.values():
                self.assertIn(value, message)
            self.assertNotIn("QA", message)
            self.assertNotIn("internal-fp.com", message)
        self.assertEqual(application.deal_url("123"), "https://crm.production.example/dashboard/sales-pipeline/deal?id=123")

    def test_another_crm_origin_is_rejected(self):
        application = application_environment()
        application.validate_deal_url(application.deal_url("123"))
        for url in ["https://production.example/deal?id=123", "http://qa.example/deal?id=123", "https://qa.example:444/deal?id=123"]:
            with self.assertRaisesRegex(ValueError, "FP_BASE_URL origin"):
                application.validate_deal_url(url)

    def test_runner_storage_is_separate_for_each_environment(self):
        from openai_managed.runner import ManagedRunnerSettings

        directories = []
        for environment in ["qa", "production"]:
            values = {
                **APPLICATION_VALUES,
                "FP_ENVIRONMENT": environment,
                "FP_USER": "test-user",
                "FP_PASSWORD": "test-password",
                "OPENAI_EXECUTOR_API_KEY": "test-key",
                "OPENAI_MANAGED_RUNS_DIRECTORY": "/tmp/sasha-environments",
            }
            with patch.dict(os.environ, values, clear=True):
                settings = ManagedRunnerSettings.from_environment()
            self.assertEqual(settings.runs_directory, Path("/tmp/sasha-environments") / environment)
            self.assertNotIn("test-password", str(settings.application.to_dict()))
            directories.append(settings.runs_directory)
        self.assertNotEqual(*directories)
