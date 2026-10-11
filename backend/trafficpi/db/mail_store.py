"""The daily email's settings and tables: recipients, each scheduled mail, each copy.

A scheduled mail is one ``mail_sends`` row (the stretch it covers and the mail as it was made, so
that every recipient and every retry gets the same words), with one ``mail_deliveries`` row for each
recipient, made in the same transaction. A delivery is ``pending``; a send in progress *claims* it
(``sending``), which is a compare-and-set, so that two takers never send one copy twice; it then
ends ``sent`` or ``failed``, or goes back to ``pending`` with a time to try again. A claim that is
never finished (the process died) is given back after a while. Removing a recipient removes their
deliveries (the foreign key cascades): an address that is taken off the list leaves nothing behind.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import CursorResult, delete, exists, func, select, update
from sqlalchemy.exc import IntegrityError

from trafficpi.db.models import MailDelivery, MailRecipient, MailSend
from trafficpi.db.store import Store
from trafficpi.mail.settings import (
    ALL_DAYS,
    DAYS_KEY,
    DEFAULT_SENDER_NAME,
    DEFAULT_TIME,
    ENABLED_KEY,
    ENABLED_SINCE_KEY,
    LINK_KEY,
    MAX_RECIPIENTS,
    SENDER_ADDRESS_KEY,
    SENDER_NAME_KEY,
    TIME_KEY,
    MailSchedule,
    clean_address,
    clean_link,
    clean_name,
    parse_time,
)


class MailLimitReached(Exception):
    """The list of recipients is full."""


@dataclass(frozen=True, slots=True)
class MailRecipientRow:
    id: int
    address: str
    added_at: int  # ms
    last_at: int | None  # ms: the last try, a daily mail or a test
    last_kind: str | None  # "daily" or "test"
    last_status: str | None  # "sent", "failed" or "retrying"
    last_detail: str | None  # the reason, when it was not sent


@dataclass(frozen=True, slots=True)
class MailSendRow:
    id: int
    scheduled_at: int  # ms: the instant this mail was for
    period_start: int  # ms
    period_end: int  # ms
    created_at: int  # ms
    status: str  # "sending", "done" or "skipped"
    note: str | None  # why it was skipped
    subject: str
    sent: int  # copies, by how they stand now
    failed: int
    pending: int  # waiting, or being sent
    cancelled: int


@dataclass(frozen=True, slots=True)
class DueDelivery:
    """A copy that is ready to be sent, with the mail it carries."""

    id: int
    send_id: int
    recipient_id: int
    address: str
    attempts: int  # how many times it has been claimed already
    scheduled_at: int  # ms
    sender: str
    subject: str
    text: str
    html: str


def _recipient(r: MailRecipient) -> MailRecipientRow:
    return MailRecipientRow(
        r.id, r.address, r.added_at, r.last_at, r.last_kind, r.last_status, r.last_detail
    )


def _rowcount(result: Any) -> int:
    return cast("CursorResult[Any]", result).rowcount


class MailStore:
    """The daily email's data, read and written on the database of the ``Store`` it is given."""

    def __init__(self, store: Store) -> None:
        self._store = store

    # --- the schedule, in the settings table -------------------------------------------------

    def get_schedule(self) -> MailSchedule:
        """The schedule as stored. What is stored but unusable is read as its safe default, and a
        schedule that is on without a usable sender address is read as off: nothing is mailed on
        the strength of a damaged value."""
        get = self._store.get_setting

        def cleaned(check: Any, value: Any, default: Any) -> Any:
            try:
                return check(value) if isinstance(value, str) else default
            except ValueError:
                return default

        time = cleaned(lambda v: (parse_time(v), v)[1], get(TIME_KEY, DEFAULT_TIME), DEFAULT_TIME)
        days: tuple[int, ...] = ALL_DAYS
        stored_days = get(DAYS_KEY, list(ALL_DAYS))
        if isinstance(stored_days, list):
            try:
                days = MailSchedule(days=tuple(stored_days)).days
            except (ValueError, TypeError):
                days = ALL_DAYS
        address = cleaned(clean_address, get(SENDER_ADDRESS_KEY, ""), "")
        return MailSchedule(
            enabled=get(ENABLED_KEY, False) is True and address != "",
            time=time,
            days=days,
            sender_name=cleaned(
                clean_name, get(SENDER_NAME_KEY, DEFAULT_SENDER_NAME), DEFAULT_SENDER_NAME
            ),
            sender_address=address,
            link_url=cleaned(clean_link, get(LINK_KEY, ""), ""),
        )

    def set_schedule(self, schedule: MailSchedule, now_ms: int) -> None:
        """Keep the schedule. Switching the email on is noted (``enabled_since``): a mail is never
        sent for a scheduled time that came before it was switched on."""
        was_on = self._store.get_setting(ENABLED_KEY, False) is True
        self._store.set_setting(TIME_KEY, schedule.time)
        self._store.set_setting(DAYS_KEY, list(schedule.days))
        self._store.set_setting(SENDER_NAME_KEY, schedule.sender_name)
        self._store.set_setting(SENDER_ADDRESS_KEY, schedule.sender_address)
        self._store.set_setting(LINK_KEY, schedule.link_url)
        if schedule.enabled and not was_on:
            self._store.set_setting(ENABLED_SINCE_KEY, now_ms)
        self._store.set_setting(ENABLED_KEY, schedule.enabled)

    def enabled_since(self) -> int:
        """When the email was last switched on, in ms; 0 if it never was."""
        value = self._store.get_setting(ENABLED_SINCE_KEY, 0)
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    # --- recipients --------------------------------------------------------------------------

    def recipients(self) -> list[MailRecipientRow]:
        with self._store.transaction() as s:
            found = s.execute(select(MailRecipient).order_by(MailRecipient.id)).scalars()
            return [_recipient(r) for r in found]

    def recipient(self, recipient_id: int) -> MailRecipientRow | None:
        with self._store.transaction() as s:
            found = s.get(MailRecipient, recipient_id)
            return None if found is None else _recipient(found)

    def add_recipient(self, address: str, now_ms: int) -> tuple[MailRecipientRow, bool]:
        """Add an address (already checked and in lower case). One that is there already is not
        added again: the answer is that one, and False. A full list raises ``MailLimitReached``."""
        with self._store.transaction() as s:
            existing = s.execute(
                select(MailRecipient).where(MailRecipient.address == address)
            ).scalar_one_or_none()
            if existing is not None:
                return _recipient(existing), False
            count = s.execute(select(func.count()).select_from(MailRecipient)).scalar_one()
            if count >= MAX_RECIPIENTS:
                raise MailLimitReached
            created = MailRecipient(address=address, added_at=now_ms)
            s.add(created)
            s.flush()
            return _recipient(created), True

    def remove_recipient(self, recipient_id: int) -> bool:
        with self._store.transaction() as s:
            return (
                _rowcount(s.execute(delete(MailRecipient).where(MailRecipient.id == recipient_id)))
                > 0
            )

    def note_result(
        self, recipient_id: int, kind: str, status: str, detail: str | None, now_ms: int
    ) -> None:
        """Keep how the last try with this recipient went (a daily mail or a test)."""
        with self._store.transaction() as s:
            s.execute(
                update(MailRecipient)
                .where(MailRecipient.id == recipient_id)
                .values(last_at=now_ms, last_kind=kind, last_status=status, last_detail=detail)
            )

    # --- scheduled mails ---------------------------------------------------------------------

    def last_scheduled_at(self) -> int | None:
        """The latest scheduled instant that has a row, whatever became of it."""
        with self._store.transaction() as s:
            return s.execute(select(func.max(MailSend.scheduled_at))).scalar_one()

    def add_send(
        self,
        *,
        scheduled_at: int,
        period_start: int,
        period_end: int,
        now_ms: int,
        status: str,
        note: str | None,
        sender: str,
        subject: str,
        text: str,
        html: str,
        recipient_ids: Sequence[int],
    ) -> int | None:
        """Make the row for a scheduled mail and a copy for each recipient, in one transaction.
        None, and nothing made, if there is a row for this instant already."""
        try:
            with self._store.transaction() as s:
                if s.execute(
                    select(MailSend.id).where(MailSend.scheduled_at == scheduled_at)
                ).first():
                    return None
                made = MailSend(
                    scheduled_at=scheduled_at,
                    period_start=period_start,
                    period_end=period_end,
                    created_at=now_ms,
                    status=status,
                    note=note,
                    sender=sender,
                    subject=subject,
                    body_text=text,
                    body_html=html,
                )
                s.add(made)
                s.flush()
                for recipient_id in recipient_ids:
                    s.add(
                        MailDelivery(
                            send_id=made.id,
                            recipient_id=recipient_id,
                            status="pending",
                            attempts=0,
                            next_attempt_at=now_ms,
                        )
                    )
                return made.id
        except IntegrityError:  # another writer made the row for this instant first
            return None

    def sends(self, limit: int = 10) -> list[MailSendRow]:
        """The newest scheduled mails first, each with how its copies stand."""
        with self._store.transaction() as s:
            found = list(
                s.execute(
                    select(MailSend).order_by(MailSend.scheduled_at.desc()).limit(limit)
                ).scalars()
            )
            counts: dict[int, dict[str, int]] = {}
            if found:
                rows = s.execute(
                    select(MailDelivery.send_id, MailDelivery.status, func.count())
                    .where(MailDelivery.send_id.in_([f.id for f in found]))
                    .group_by(MailDelivery.send_id, MailDelivery.status)
                )
                for send_id, status, n in rows:
                    counts.setdefault(send_id, {})[status] = n
            result = []
            for f in found:
                c = counts.get(f.id, {})
                result.append(
                    MailSendRow(
                        id=f.id,
                        scheduled_at=f.scheduled_at,
                        period_start=f.period_start,
                        period_end=f.period_end,
                        created_at=f.created_at,
                        status=f.status,
                        note=f.note,
                        subject=f.subject,
                        sent=c.get("sent", 0),
                        failed=c.get("failed", 0),
                        pending=c.get("pending", 0) + c.get("sending", 0),
                        cancelled=c.get("cancelled", 0),
                    )
                )
            return result

    def close_finished_sends(self) -> int:
        """Mark done each mail all of whose copies have gone one way or the other."""
        waiting = exists().where(
            MailDelivery.send_id == MailSend.id, MailDelivery.status.in_(("pending", "sending"))
        )
        with self._store.transaction() as s:
            return _rowcount(
                s.execute(
                    update(MailSend)
                    .where(MailSend.status == "sending", ~waiting)
                    .values(status="done")
                )
            )

    def delete_sends_before(self, cutoff_ms: int, *, limit: int = 200) -> int:
        """Delete up to ``limit`` mails scheduled before ``cutoff_ms``, with their copies."""
        oldest = (
            select(MailSend.id)
            .where(MailSend.scheduled_at < cutoff_ms)
            .order_by(MailSend.scheduled_at)
            .limit(limit)
        )
        with self._store.transaction() as s:
            return _rowcount(s.execute(delete(MailSend).where(MailSend.id.in_(oldest))))

    # --- copies ------------------------------------------------------------------------------

    def due_deliveries(self, now_ms: int, limit: int = 50) -> list[DueDelivery]:
        """Copies that are waiting and whose time has come, oldest first."""
        with self._store.transaction() as s:
            rows = s.execute(
                select(
                    MailDelivery.id,
                    MailDelivery.send_id,
                    MailDelivery.recipient_id,
                    MailRecipient.address,
                    MailDelivery.attempts,
                    MailSend.scheduled_at,
                    MailSend.sender,
                    MailSend.subject,
                    MailSend.body_text,
                    MailSend.body_html,
                )
                .join(MailSend, MailSend.id == MailDelivery.send_id)
                .join(MailRecipient, MailRecipient.id == MailDelivery.recipient_id)
                .where(MailDelivery.status == "pending", MailDelivery.next_attempt_at <= now_ms)
                .order_by(MailDelivery.id)
                .limit(limit)
            )
            return [DueDelivery(*row) for row in rows]

    def claim(self, delivery_id: int, now_ms: int) -> bool:
        """Take a waiting copy to send it: True for one taker only. It counts as an attempt, and
        the time of the claim is kept so that a claim that is never finished can be given back."""
        with self._store.transaction() as s:
            return (
                _rowcount(
                    s.execute(
                        update(MailDelivery)
                        .where(MailDelivery.id == delivery_id, MailDelivery.status == "pending")
                        .values(
                            status="sending",
                            attempts=MailDelivery.attempts + 1,
                            next_attempt_at=now_ms,
                        )
                    )
                )
                == 1
            )

    def finish(
        self,
        delivery_id: int,
        kind: str,
        now_ms: int,
        *,
        detail: str | None = None,
        provider_id: str | None = None,
        retry_at: int | None = None,
    ) -> bool:
        """Record how an attempt ended: ``sent``, ``failed`` (with the reason), or ``retry`` (with
        the time to try again). Only a copy that was claimed can be finished; False otherwise."""
        if kind == "sent":
            values: dict[str, Any] = {
                "status": "sent",
                "sent_at": now_ms,
                "provider_id": provider_id,
                "last_error": None,
                "next_attempt_at": None,
            }
        elif kind == "failed":
            values = {"status": "failed", "last_error": detail, "next_attempt_at": None}
        elif kind == "retry" and retry_at is not None:
            values = {"status": "pending", "last_error": detail, "next_attempt_at": retry_at}
        else:
            raise ValueError(f"cannot finish a delivery as {kind!r}")
        with self._store.transaction() as s:
            return (
                _rowcount(
                    s.execute(
                        update(MailDelivery)
                        .where(MailDelivery.id == delivery_id, MailDelivery.status == "sending")
                        .values(**values)
                    )
                )
                == 1
            )

    def release_stuck(self, now_ms: int, *, older_than_ms: int) -> int:
        """Give back the copies claimed more than ``older_than_ms`` ago and never finished: the
        process that took them died. Each is waiting again, and the attempt it made still counts."""
        with self._store.transaction() as s:
            return _rowcount(
                s.execute(
                    update(MailDelivery)
                    .where(
                        MailDelivery.status == "sending",
                        MailDelivery.next_attempt_at < now_ms - older_than_ms,
                    )
                    .values(status="pending", next_attempt_at=now_ms)
                )
            )

    def cancel_pending(self) -> int:
        """Stop the copies that are waiting (the email was switched off); one in flight finishes."""
        with self._store.transaction() as s:
            return _rowcount(
                s.execute(
                    update(MailDelivery)
                    .where(MailDelivery.status == "pending")
                    .values(status="cancelled", next_attempt_at=None)
                )
            )

    def expire_late(self, now_ms: int, max_late_ms: int, detail: str) -> int:
        """Fail the waiting copies of mails scheduled more than ``max_late_ms`` ago, with ``detail``
        as the reason, and tell each recipient: a report that is a day old is not sent."""
        with self._store.transaction() as s:
            late = list(
                s.execute(
                    select(MailDelivery.id, MailDelivery.recipient_id)
                    .join(MailSend, MailSend.id == MailDelivery.send_id)
                    .where(
                        MailDelivery.status == "pending",
                        MailSend.scheduled_at < now_ms - max_late_ms,
                    )
                )
            )
            for delivery_id, recipient_id in late:
                s.execute(
                    update(MailDelivery)
                    .where(MailDelivery.id == delivery_id)
                    .values(status="failed", last_error=detail, next_attempt_at=None)
                )
                s.execute(
                    update(MailRecipient)
                    .where(MailRecipient.id == recipient_id)
                    .values(
                        last_at=now_ms, last_kind="daily", last_status="failed", last_detail=detail
                    )
                )
            return len(late)
