"""Hand final messages to FreshFlow and store email thread callbacks."""

from dataclasses import replace
import time

from sasha_database.messages import valid_email

from .content import customer_subject, phone_number, prepare_sms, root_subject
from .models import OutboundMessage, EmailRequest, email_trace_id, message_trace_id
from .payloads import ASSIGNMENTS, email_payload, sms_attributes, sms_payload
from .repository import OutboundRepository
from .settings import OutboundSettings, is_enabled
from .transport import SqsTransport


class OutboundService:
    def __init__(self, storage, settings, repository=None, transport=None, sleep=time.sleep, report=None):
        self.storage = storage
        self.settings = settings
        self.repository = repository or OutboundRepository(storage)
        self.transport = transport
        self.sleep = sleep
        self.report = report or (lambda message: None)

    @classmethod
    def from_environment(cls, storage=None, report=None):
        if not is_enabled():
            return None
        settings = OutboundSettings.from_environment()
        if storage is None:
            from sasha_database.storage import SashaStorage
            storage = SashaStorage()
        return cls(storage, settings, report=report)

    @classmethod
    def for_callbacks(cls):
        # Thread callbacks remain useful when new sending is disabled.
        from sasha_database.storage import SashaStorage
        return cls(SashaStorage(), settings=None)

    def prepare_result(self, task, result):
        if result.deal_id != task.deal_id:
            raise ValueError("Outbound result does not belong to this task's deal")
        if result.status not in {"completed", "handoff"} or not result.has_draft():
            return result, []
        contact = self.repository.contact(task.deal_id)
        contact["phone"] = phone_number(contact.get("phone") or "")
        thread = self._customer_thread(task, result)
        subject = customer_subject(task, result, thread) if result.message_html.strip() else ""
        result = replace(result, email_subject=subject, sms_messages=prepare_sms(result.sms_messages, self.report))
        purpose = "customer_holding" if result.status == "handoff" else "customer"
        messages = []
        if result.message_html.strip():
            request = EmailRequest(task.deal_id, task.run_id, purpose, contact.get("email") or "",
                                   subject, result.message_html, thread.get("thread_id"), convo_type=result.convo_type)
            messages.append(self.prepare_email(request, contact))
        for position, bubble in enumerate(result.sms_messages):
            messages.append(self._prepare_sms(task, result, contact, purpose, position, bubble))
        return result, messages

    def _customer_thread(self, task, result):
        if result.convo_type == "outreach":
            return {}
        if result.convo_type == "client_response":
            for message in reversed(task.incoming_messages):
                if (message.message_type == "EMAIL" and message.convo_type != "internal_mail"
                        and message.external_thread_id):
                    subject = message.subject
                    if not root_subject(subject):
                        saved = self.repository.client_thread(task.deal_id, message.external_thread_id)
                        subject = saved.get("subject")
                    return {"thread_id": message.external_thread_id, "subject": subject}
        return self.repository.client_thread(task.deal_id)

    def prepare_email(self, request, contact=None):
        contact = contact if contact is not None else self.repository.contact(request.deal_id)
        trace = email_trace_id("offer", request.offer_id) if request.offer_id else ""
        if request.purpose == "handoff_offer" and not request.offer_id:
            raise ValueError("An ISA email requires its existing offer ID")
        assignment = "handoff_offer" if request.purpose == "handoff_offer" else ASSIGNMENTS[request.convo_type]
        if request.purpose == "customer_transition":
            assignment = ""
        payload = email_payload(
            trace_id=trace, deal_id=request.deal_id, account_id=contact.get("account_id"),
            assignment=assignment, sender=self.settings.sender, recipient=request.recipient,
            subject=request.subject, body=request.body, thread_id=request.thread_id, cc=request.cc)
        if not valid_email(request.recipient) or not all(valid_email(address) for address in request.cc):
            raise ValueError("Missing or invalid email recipient")
        return OutboundMessage(trace, request.deal_id, request.source_id, request.purpose, "EMAIL", 0,
                               request.recipient, payload, offer_id=request.offer_id)

    def _prepare_sms(self, task, result, contact, purpose, position, bubble):
        trace = message_trace_id(self.settings.environment, task.deal_id, task.run_id, purpose, "SMS", position)
        payload = sms_payload(trace_id=trace, contact=contact, message=bubble["text"],
                              assignment=ASSIGNMENTS[result.convo_type])
        if not contact["phone"]:
            raise ValueError("Missing or invalid SMS recipient")
        return OutboundMessage(trace, task.deal_id, task.run_id, purpose, "SMS", position, contact["phone"],
                               payload, sms_attributes(task.deal_id, contact.get("account_id")))

    def submit_result(self, task, result, *, previous_notes, notes):
        if result.status == "failed":
            return result, {"enabled": True, "status": "none"}
        result, messages = self.prepare_result(task, result)
        self.storage.complete_run(
            task.deal_id, task.run_id, previous_notes, notes, result.message_html, result.convo_type,
            handoff_reason=result.handoff_reason if result.status == "handoff" else None,
            deal_state=None if result.status == "handoff" else result.deal_state,
            sms_messages=result.sms_messages,
            prepare_outbound=lambda cursor, history_ids: self.repository.prepare_saved_emails(cursor, messages, history_ids))
        return result, self._submit_messages(messages)

    def submit_email(self, request):
        message = self.prepare_email(request)
        if request.purpose != "handoff_offer":
            self.storage.record_outbound(
                request.deal_id, request.source_id, request.convo_type, request.body,
                subject=request.subject, external_thread_id=request.thread_id,
                prepare_outbound=lambda cursor, ids: self.repository.prepare_saved_emails(cursor, [message], ids))
        return self._submit_messages([message])

    def _submit_messages(self, messages):
        if not messages:
            return {"enabled": True, "status": "none"}
        try:
            self._publish(messages)
        except Exception as error:
            self.report("[outbound] FreshFlow submission failed: " + type(error).__name__)
            return {"enabled": True, "status": "outbound_failed", "error": type(error).__name__}
        return {"enabled": True, "status": "submitted"}

    def _publish(self, messages):
        if self.transport is None:
            self.transport = SqsTransport(self.settings)
        for message in messages:
            if message.channel == "SMS" and message.position > 0:
                self.sleep(2)
            self.transport.publish({"payload": message.payload, "attributes": message.attributes})

    def store_thread_id(self, callback):
        return self.repository.store_thread_id(callback)
