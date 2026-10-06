"""Job handlers: the work that must survive a crash after acknowledgement.

    process_inbound   inbound WhatsApp message -> orchestrator -> send_message
    send_message      deliver a text through the channel transport
    provider_submit   submit an accepted quote to its provider (idempotency key)
    provider_cancel   ask the provider to cancel
    notify_guest      tell the guest the new stored state of a transaction
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent import messages as msg
from app.clock import Clock
from app.db.models import Job, Message, MessageRole
from app.jobs.queue import JobWorker, PermanentJobError, RetryableJobError, enqueue
from app.observability import log_event
from app.schemas.messages import InboundMessage
from app.transactions.service import TransactionService

if TYPE_CHECKING:
    from app.container import Container


def _stored_reply(session: Session, external_id: str) -> str | None:
    """The bot reply already produced for an inbound message (crash recovery:
    the message was handled but the reply was not yet queued)."""
    guest = session.scalar(select(Message).where(Message.external_id == external_id))
    if guest is None:
        return None
    reply = session.scalar(select(Message).where(Message.conversation_id == guest.conversation_id,
                                                 Message.seq > guest.seq, Message.role == MessageRole.BOT)
                           .order_by(Message.seq).limit(1))
    return reply.text if reply else None


def register_handlers(worker: JobWorker, container: Container) -> None:
    deps = container.txn_deps
    clock: Clock = container.clock

    def process_inbound(s: Session, job: Job) -> None:
        p = job.payload
        if p.get("text") is None:
            reply_text = msg.t("unsupported_media", "en")
        else:
            runtime = container.properties.for_whatsapp_number(p.get("phone_number_id"))
            reply = container.orchestrator.handle(InboundMessage(
                channel="whatsapp", sender_id=p["sender_id"], text=p["text"], external_id=p["message_id"],
                display_name=p.get("display_name"), property_slug=runtime.slug))
            reply_text = reply.text if not reply.duplicate else _stored_reply(s, p["message_id"])
        if reply_text:
            enqueue(s, "send_message", {"channel": "whatsapp", "to": p["sender_id"], "text": reply_text},
                    f"reply:{p['message_id']}", clock=clock)

    def send_message(s: Session, job: Job) -> None:
        p = job.payload
        transport = container.transport_for(p["channel"])
        result = transport.send_text(p["to"], p["text"])
        log_event("message_sent", channel=p["channel"], transport=transport.name, ok=result.ok,
                  message_id=result.message_id, error=result.error)
        if not result.ok:
            raise RetryableJobError(f"delivery failed: {result.error}")

    def provider_submit(s: Session, job: Job) -> None:
        TransactionService(s, deps).run_submit(job.payload["transaction_id"])

    def provider_submit_dead(s: Session, job: Job, error: str) -> None:
        TransactionService(s, deps).on_submit_dead(job.payload["transaction_id"], error)

    def provider_cancel(s: Session, job: Job) -> None:
        TransactionService(s, deps).run_cancel(job.payload["transaction_id"], job.payload.get("template"))

    def provider_cancel_dead(s: Session, job: Job, error: str) -> None:
        TransactionService(s, deps).on_cancel_dead(job.payload["transaction_id"], error)

    def notify_guest(s: Session, job: Job) -> None:
        p = job.payload
        if not p.get("action_id"):
            raise PermanentJobError("notify_guest without action_id")
        TransactionService(s, deps).run_notify(p["action_id"], p["status"], p.get("template"))

    worker.register("process_inbound", process_inbound)
    worker.register("send_message", send_message)
    worker.register("provider_submit", provider_submit, on_dead=provider_submit_dead)
    worker.register("provider_cancel", provider_cancel, on_dead=provider_cancel_dead)

    def provider_reconcile(s: Session, job: Job) -> None:
        TransactionService(s, deps).run_reconcile(job.payload["transaction_id"])

    worker.register("provider_reconcile", provider_reconcile)
    worker.register("notify_guest", notify_guest)
