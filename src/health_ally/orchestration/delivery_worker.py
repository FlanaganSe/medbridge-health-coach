"""Outbox delivery worker — polls outbox entries and dispatches to channels.

Uses SELECT ... FOR UPDATE SKIP LOCKED (same pattern as scheduler).
Orders by priority DESC, created_at ASC (urgent alerts first).

AD-5: Consent re-check before transport for patient_message only.
       Clinician alerts skip consent re-check.
AD-6: Creates DeliveryAttempt record per transport attempt.
"""

# pyright: reportUnknownVariableType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false

from __future__ import annotations

import asyncio
import random
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import func, select, update

from health_ally.integrations.notification import DeliveryResult
from health_ally.persistence.models import (
    AuditEvent,
    ClinicianAlert,
    DeliveryAttempt,
    OutboxEntry,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from health_ally.domain.consent import ConsentService
    from health_ally.integrations.alert_channel import AlertChannel
    from health_ally.integrations.notification import NotificationChannel

logger = structlog.stdlib.get_logger()

_DEFAULT_BATCH_SIZE = 20
_JITTER_FRACTION = 0.2
_MAX_DELIVERY_ATTEMPTS = 5
_STUCK_THRESHOLD_MINUTES = 5


class DeliveryWorker:
    """Background worker that delivers outbox entries to notification channels."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        consent_service: ConsentService,
        notification_channel: NotificationChannel,
        alert_channel: AlertChannel,
        poll_interval_seconds: int = 5,
        batch_size: int = _DEFAULT_BATCH_SIZE,
    ) -> None:
        self._session_factory = session_factory
        self._consent_service = consent_service
        self._notification_channel = notification_channel
        self._alert_channel = alert_channel
        self._poll_interval = poll_interval_seconds
        self._batch_size = batch_size
        self._shutdown_event = asyncio.Event()

    @property
    def shutdown_event(self) -> asyncio.Event:
        """Event to signal graceful shutdown."""
        return self._shutdown_event

    async def run(self) -> None:
        """Main poll loop — runs until shutdown_event is set."""
        # Recover stuck entries on startup
        await self._recover_stuck_entries()
        await logger.ainfo("delivery_worker_started", poll_interval=self._poll_interval)

        while not self._shutdown_event.is_set():
            try:
                processed = await self._poll_and_deliver()
                if processed > 0:
                    await logger.ainfo("delivery_batch_processed", count=processed)
            except Exception:
                logger.exception("delivery_poll_error")

            jitter = self._poll_interval * random.uniform(  # noqa: S311
                1 - _JITTER_FRACTION, 1 + _JITTER_FRACTION
            )
            try:
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=jitter)
                break
            except TimeoutError:
                continue

        await logger.ainfo("delivery_worker_stopped")

    async def _recover_stuck_entries(self) -> None:
        """Reset entries stuck in 'delivering' back to 'pending'.

        Called on startup and periodically to recover from worker crashes.
        """
        cutoff = datetime.now(UTC) - timedelta(minutes=_STUCK_THRESHOLD_MINUTES)
        async with self._session_factory() as session, session.begin():
            result = await session.execute(
                update(OutboxEntry)
                .where(
                    OutboxEntry.status == "delivering",
                    OutboxEntry.updated_at <= cutoff,
                )
                .values(status="pending")
            )
            count = result.rowcount  # type: ignore[assignment]
        if count and count > 0:
            await logger.awarning("delivery_recovered_stuck_entries", count=count)

    async def _poll_and_deliver(self) -> int:
        """Claim pending outbox entries and deliver them."""
        async with self._session_factory() as session, session.begin():
            stmt = (
                select(OutboxEntry)
                .where(OutboxEntry.status == "pending")
                .order_by(OutboxEntry.priority.desc(), OutboxEntry.created_at)
                .limit(self._batch_size)
                .with_for_update(skip_locked=True)
            )
            result = await session.execute(stmt)
            entries = list(result.scalars().all())

            if not entries:
                return 0

            entry_ids = [e.id for e in entries]
            await session.execute(
                update(OutboxEntry)
                .where(OutboxEntry.id.in_(entry_ids))
                .values(status="delivering", updated_at=func.now())
            )

        for entry in entries:
            await self._deliver_single(entry)

        return len(entries)

    async def _deliver_single(self, entry: OutboxEntry) -> None:
        """Deliver a single outbox entry with consent check and retry logic."""
        patient_id = str(entry.patient_id)

        # AD-5: Consent re-check for patient messages only
        if entry.message_type == "patient_message":
            consent = await self._consent_service.check(patient_id, entry.tenant_id)
            if not consent.allowed:
                await self._cancel_entry(entry, consent.reason)
                return

        start = time.monotonic()
        try:
            if entry.message_type == "clinician_alert":
                delivery_result = await self._deliver_alert(entry)
            else:
                delivery_result = await self._deliver_message(entry)

            latency_ms = int((time.monotonic() - start) * 1000)

            attempt_number = await self._record_attempt(
                entry,
                outcome="success" if delivery_result.success else "failed",
                receipt=delivery_result.receipt if delivery_result.success else None,
                error=delivery_result.error,
                latency_ms=latency_ms,
            )

            if delivery_result.success:
                await self._mark_entry(entry.id, "delivered")
            else:
                await self._handle_delivery_failure(entry, attempt_number)

        except Exception as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            logger.exception(
                "delivery_error",
                outbox_id=str(entry.id),
                patient_id=patient_id,
            )
            attempt_number = await self._record_attempt(
                entry,
                outcome="failed",
                error=str(exc),
                latency_ms=latency_ms,
            )
            await self._handle_delivery_failure(entry, attempt_number)

    async def _deliver_message(self, entry: OutboxEntry) -> DeliveryResult:
        """Deliver a patient-facing message."""
        payload = entry.payload or {}
        message = str(payload.get("message", ""))
        if not message:
            return DeliveryResult(success=False, error="empty_message")

        return await self._notification_channel.send(
            message=message,
            patient_id=str(entry.patient_id),
            metadata=payload,
        )

    async def _deliver_alert(self, entry: OutboxEntry) -> DeliveryResult:
        """Deliver a clinician alert, matched by delivery_key."""
        async with self._session_factory() as session, session.begin():
            alert = await session.execute(
                select(ClinicianAlert).where(
                    ClinicianAlert.idempotency_key == entry.delivery_key,
                )
            )
            alert_row = alert.scalars().first()

        if alert_row is None:
            await logger.awarning(
                "delivery_alert_not_found",
                outbox_id=str(entry.id),
                delivery_key=entry.delivery_key,
            )
            return DeliveryResult(success=False, error="alert_not_found")

        return await self._alert_channel.send_alert(alert_row)

    async def _cancel_entry(self, entry: OutboxEntry, reason: str) -> None:
        """Cancel delivery due to consent failure."""
        async with self._session_factory() as session, session.begin():
            await session.execute(
                update(OutboxEntry).where(OutboxEntry.id == entry.id).values(status="cancelled")
            )
            session.add(
                AuditEvent(
                    tenant_id=entry.tenant_id,
                    patient_id=entry.patient_id,
                    event_type="delivery_cancelled",
                    outcome="consent_denied",
                    metadata_={"reason": reason, "outbox_id": str(entry.id)},
                )
            )

        await logger.ainfo(
            "delivery_cancelled_consent",
            outbox_id=str(entry.id),
            patient_id=str(entry.patient_id),
            reason=reason,
        )

    async def _mark_entry(self, entry_id: object, status: str) -> None:
        """Update outbox entry status."""
        async with self._session_factory() as session, session.begin():
            await session.execute(
                update(OutboxEntry).where(OutboxEntry.id == entry_id).values(status=status)
            )

    async def _handle_delivery_failure(self, entry: OutboxEntry, attempt_number: int) -> None:
        """Dead-letter if max attempts exceeded, otherwise reset to pending."""
        if attempt_number >= _MAX_DELIVERY_ATTEMPTS:
            await self._mark_entry(entry.id, "dead")
            await logger.awarning(
                "delivery_dead_letter",
                outbox_id=str(entry.id),
                patient_id=str(entry.patient_id),
                attempts=attempt_number,
            )
        else:
            # Reset to pending for retry
            await self._mark_entry(entry.id, "pending")

    async def _record_attempt(
        self,
        entry: OutboxEntry,
        *,
        outcome: str,
        receipt: dict[str, object] | None = None,
        error: str | None = None,
        latency_ms: int = 0,
    ) -> int:
        """Record a delivery attempt. Returns the attempt number."""
        async with self._session_factory() as session, session.begin():
            # Count + insert in same transaction to avoid race conditions
            count_result = await session.execute(
                select(func.count())
                .select_from(DeliveryAttempt)
                .where(DeliveryAttempt.outbox_entry_id == entry.id)
            )
            attempt_number = (count_result.scalar() or 0) + 1

            session.add(
                DeliveryAttempt(
                    tenant_id=entry.tenant_id,
                    outbox_entry_id=entry.id,
                    attempt_number=attempt_number,
                    outcome=outcome,
                    delivery_receipt=receipt,
                    error=error,
                    latency_ms=latency_ms,
                )
            )

        return attempt_number
