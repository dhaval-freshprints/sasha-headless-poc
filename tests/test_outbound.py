"""Outbound contracts, subject behavior, and transport failures; no live sends."""

from dataclasses import replace
import json
import unittest
from unittest.mock import Mock, patch

import boto3
from botocore.exceptions import ClientError, MissingDependencyException, ReadTimeoutError
from fastapi import FastAPI
from fastapi.testclient import TestClient

from account_manager_agent.task import SashaResult, SashaTask
from outbound.api import create_router
from outbound.content import customer_subject
from outbound.models import EmailRequest
from outbound.service import OutboundService
from outbound.settings import OutboundSettings
from outbound.transport import PublishError, SqsTransport
from sasha_database.messages import IncomingMessage


SETTINGS = OutboundSettings("qa", "https://sqs.us-east-1.amazonaws.com/123456789012/test", "us-east-1", "sasha@example.com")
CONTACT = {"email": "client@example.com", "phone": "4155550100", "first_name": "Casey", "last_name": "Test", "account_id": 12}


def task(**changes):
    return SashaTask("42", "task", "https://qa.example/deal/42", run_id="run-42", **changes)


def result(channel="both", **changes):
    values = dict(message_html="<p>Your proof is ready.</p>" if channel in {"email", "both"} else "",
                  sms_messages=[{"category": "core_response", "text": "Your proof is ready."}]
                  if channel in {"sms", "both"} else [], communication_channel=channel,
                  email_subject="Your proof", convo_type="client_response" if channel != "none" else None)
    return SashaResult("42", "completed", **{**values, **changes})


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.repository = Mock()
        self.repository.contact.side_effect = lambda deal: dict(CONTACT)
        self.repository.client_thread.return_value = {"thread_id": "customer-thread", "subject": "Original subject"}
        self.service = OutboundService(Mock(), SETTINGS, repository=self.repository)

    def test_channels_and_payloads(self):
        for channel, expected in (("email", ["EMAIL"]), ("sms", ["SMS"]), ("both", ["EMAIL", "SMS"]), ("none", [])):
            with self.subTest(channel=channel):
                _, messages = self.service.prepare_result(task(), result(channel))
                self.assertEqual([item.channel for item in messages], expected)
        _, messages = self.service.prepare_result(task(), result())
        email, sms = messages
        self.assertEqual(email.payload["type"], "sasha-email-trigger")
        metadata = email.payload["payload"]
        self.assertEqual(metadata["account_id"], "12")
        self.assertIsNone(metadata["sasha_output_id"])
        self.assertEqual(metadata["assignment_type"], "CLIENT_RESPONSE")
        draft = metadata["workflow_result"]["result"]["draft"]
        self.assertEqual(draft, {"email_subject": "Re: Original subject", "email_body": "<p>Your proof is ready.</p>",
                                "recipient_email": CONTACT["email"], "sender_email": SETTINGS.sender,
                                "thread_id": "customer-thread", "cc_email": []})
        self.assertEqual(sms.payload["payload"]["phoneNumber"], "+14155550100")
        self.assertEqual(sms.attributes, {"source": "sasha_ai", "type": "send-text-message", "deal_id": "42", "account_id": "12"})
        self.assertNotIn("deal_id", sms.payload["payload"])
        self.assertNotEqual(email.trace_id, sms.trace_id)

    def test_outreach_starts_new_thread_with_fixed_subject(self):
        for generated_subject in ("", "Merch for your Class Council"):
            with self.subTest(generated_subject=generated_subject):
                prepared, messages = self.service.prepare_result(
                    task(), result(convo_type="outreach", email_subject=generated_subject))
                self.assertEqual(prepared.email_subject, "Fresh Prints here to help!")
                draft = messages[0].payload["payload"]["workflow_result"]["result"]["draft"]
                self.assertEqual(draft["email_subject"], "Fresh Prints here to help!")
                self.assertIsNone(draft["thread_id"])
        self.repository.client_thread.assert_not_called()

    def test_reply_uses_customer_inbound_thread_but_not_team_thread(self):
        inbound = IncomingMessage("Hello", sender_name="client@example.com", subject="Re: Fwd: Original",
                                  external_thread_id="inbound-thread")
        prepared, messages = self.service.prepare_result(task(incoming_messages=(inbound,)), result("email"))
        self.assertEqual(prepared.email_subject, "Re: Original")
        self.assertEqual(messages[0].payload["payload"]["workflow_result"]["result"]["draft"]["thread_id"], "inbound-thread")
        team = replace(inbound, sender_name="team@freshprints.com", convo_type="internal_mail")
        prepared, messages = self.service.prepare_result(task(incoming_messages=(team,)), result("email", convo_type="internal_mail"))
        self.assertEqual(prepared.email_subject, "Re: Original subject")
        self.assertEqual(messages[0].payload["payload"]["workflow_result"]["result"]["draft"]["thread_id"], "customer-thread")

    def test_followup_reuses_root_and_failed_result_never_sends(self):
        prepared, _ = self.service.prepare_result(task(), result("email", convo_type="followup"))
        self.assertEqual(prepared.email_subject, "Re: Original subject")
        _, messages = self.service.prepare_result(task(), replace(result(), status="failed"))
        self.assertEqual(messages, [])

    def test_reply_without_saved_thread_uses_current_customer_subject_or_fixed_default(self):
        self.repository.client_thread.return_value = {}
        older = IncomingMessage("Older email", subject="Older subject")
        cases = [
            (IncomingMessage("Current email", subject=" re: FWD: Re: Fresh Prints here to help! "),
             "Re: Fresh Prints here to help!"),
            (IncomingMessage("Current email", subject=None), "Re: Fresh Prints here to help!"),
            (IncomingMessage("Current email", subject="  "), "Re: Fresh Prints here to help!"),
            (IncomingMessage("Current email", subject="Re: Fwd:"), "Re: Fresh Prints here to help!"),
            (IncomingMessage("Current SMS", message_type="SMS", sender_name="+14155550100"),
             "Re: Fresh Prints here to help!"),
        ]
        for current, expected in cases:
            with self.subTest(current=current):
                prepared, messages = self.service.prepare_result(
                    task(incoming_messages=(older, current)), result("email", email_subject="Ignore generated subject"))
                self.assertEqual(prepared.email_subject, expected)
                draft = messages[0].payload["payload"]["workflow_result"]["result"]["draft"]
                self.assertEqual(draft["email_subject"], expected)

    def test_new_thread_keeps_saved_subject_or_uses_fixed_default(self):
        for saved_subject, expected in (("Re: Fwd: Original", "Re: Original"),
                                        (None, "Re: Fresh Prints here to help!")):
            self.repository.client_thread.return_value = {"thread_id": None, "subject": saved_subject}
            for convo_type in ("client_response", "followup", "internal_mail"):
                with self.subTest(saved_subject=saved_subject, convo_type=convo_type):
                    prepared, messages = self.service.prepare_result(task(), result("email", convo_type=convo_type))
                    self.assertEqual(prepared.email_subject, expected)
                    draft = messages[0].payload["payload"]["workflow_result"]["result"]["draft"]
                    self.assertEqual(draft["email_subject"], expected)
                    self.assertIsNone(draft["thread_id"])

    def test_reply_recovers_missing_subject_from_its_own_thread(self):
        incoming = IncomingMessage("Reply", external_thread_id="incoming-thread", subject=None)
        prepared, messages = self.service.prepare_result(task(incoming_messages=(incoming,)), result("email"))
        self.repository.client_thread.assert_called_once_with("42", "incoming-thread")
        self.assertEqual(prepared.email_subject, "Re: Original subject")
        draft = messages[0].payload["payload"]["workflow_result"]["result"]["draft"]
        self.assertEqual(draft["thread_id"], "incoming-thread")

    def test_missing_recipient_is_rejected_before_submission(self):
        self.repository.contact.side_effect = lambda deal: {**CONTACT, "email": "", "phone": "4155550100"}
        with self.assertRaisesRegex(ValueError, "email recipient"):
            self.service.prepare_result(task(), result())

    def test_sms_shortening_preserves_order_and_final_text(self):
        bubbles = [{"category": "core_response", "text": "<p>One &amp; two</p>"}, {"category": "cta", "text": "Next?"}]
        with patch("outbound.content.shorten_sms_messages", side_effect=lambda messages, report: messages) as shorten:
            prepared, messages = self.service.prepare_result(task(), result("sms", sms_messages=bubbles))
        self.assertEqual(prepared.sms_messages[0]["text"], "One & two")
        self.assertEqual([item.position for item in messages], [0, 1])
        self.assertEqual([item.payload["payload"]["message"] for item in messages], ["One & two", "Next?"])
        shorten.assert_called_once()

    def test_identity_stable_and_cross_deal_result_rejected(self):
        _, first = self.service.prepare_result(task(), result())
        _, second = self.service.prepare_result(task(), result())
        self.assertEqual([item.trace_id for item in first], [item.trace_id for item in second])
        with self.assertRaisesRegex(ValueError, "does not belong"):
            self.service.prepare_result(task(), replace(result(), deal_id="99"))

    def test_disabled_service_does_not_initialize_database_or_transport(self):
        with patch("outbound.service.is_enabled", return_value=False):
            self.assertIsNone(OutboundService.from_environment())

    def test_callback_service_needs_no_queue_configuration_or_enabled_sender(self):
        with patch("sasha_database.storage.SashaStorage") as storage, \
                patch("outbound.service.is_enabled", return_value=False), \
                patch("outbound.service.OutboundSettings.from_environment") as settings:
            service = OutboundService.for_callbacks()
        self.assertIsNone(service.settings)
        self.assertEqual(service.storage, storage.return_value)
        settings.assert_not_called()

    def test_initialization_failure_reports_outbound_failed_without_retry(self):
        error = MissingDependencyException(msg="AWS login dependency missing")
        with patch("outbound.service.SqsTransport", side_effect=error) as create:
            final, outcome = self.service.submit_result(task(), result("email"), previous_notes="", notes="Notes")
        self.assertEqual(final.status, "completed")
        self.assertEqual(outcome, {"enabled": True, "status": "outbound_failed", "error": "MissingDependencyException"})
        create.assert_called_once()
        self.service.storage.complete_run.assert_called_once()

    def test_successful_submission_reports_submitted(self):
        self.service.transport = Mock()
        _, outcome = self.service.submit_result(task(), result("email"), previous_notes="", notes="Notes")
        self.assertEqual(outcome, {"enabled": True, "status": "submitted"})
        self.service.transport.publish.assert_called_once()


class TransportTests(unittest.TestCase):
    def test_envelope_is_json_and_sms_ids_are_attributes(self):
        client = Mock()
        client.send_message.return_value = {"MessageId": "sqs-1"}
        transport = SqsTransport(SETTINGS, client)
        row = {"payload": {"type": "send-text-message"}, "attributes": {"deal_id": "42"}}
        self.assertEqual(transport.publish(row), "sqs-1")
        arguments = client.send_message.call_args.kwargs
        self.assertEqual(json.loads(arguments["MessageBody"]), row["payload"])
        self.assertEqual(arguments["MessageAttributes"]["deal_id"], {"StringValue": "42", "DataType": "String"})

    def test_transport_disables_sdk_retries(self):
        client = Mock()
        with patch.object(boto3, "client", return_value=client) as create:
            SqsTransport(SETTINGS)
        self.assertEqual(create.call_args.kwargs["config"].retries["total_max_attempts"], 1)

    def test_submission_errors_are_reported_without_retrying(self):
        errors = [
            ReadTimeoutError(endpoint_url=SETTINGS.queue_url),
            ClientError({"Error": {"Code": "InternalError"}}, "SendMessage"),
            ClientError({"Error": {"Code": "RequestThrottled"}}, "SendMessage"),
        ]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                client = Mock()
                client.send_message.side_effect = error
                with self.assertRaises(PublishError):
                    SqsTransport(SETTINGS, client).publish({"payload": {}, "attributes": {}})
                client.send_message.assert_called_once()


class CallbackApiTests(unittest.TestCase):
    def test_validation_normalization_and_disabled_response(self):
        service = Mock()
        service.store_thread_id.return_value = {"status": "updated"}
        app = FastAPI()
        app.include_router(create_router(lambda: service))
        client = TestClient(app)
        response = client.post("/communication/thread-id", json={"deal_id": 42, "thread_id": " t1 ", "message_id": " "})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(service.store_thread_id.call_args.args[0]["thread_id"], "t1")
        for payload in ({"deal_id": "-1", "thread_id": "t"}, {"deal_id": 42, "thread_id": " "},
                        {"deal_id": 42, "thread_id": "t", "assignment_type": "INITIAL_OUTREACH"}):
            self.assertEqual(client.post("/communication/thread-id", json=payload).status_code, 422)
        disabled = FastAPI()
        disabled.include_router(create_router(lambda: None))
        self.assertEqual(TestClient(disabled).post("/communication/thread-id", json={"deal_id": 42, "thread_id": "t"}).status_code, 503)
