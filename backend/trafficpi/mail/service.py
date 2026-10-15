"""The daily email's service: what is due, who gets what once, retries, and tests.

``MailService.tick`` is what the API's scheduler calls about every 30 seconds, on a worker thread
(it makes HTTP calls, so it must never run on the event loop or hold up the watchdog's heartbeat).
Each look does the same few things, in this order:

1. gives back copies that a process that died had claimed, and fails copies that are too late;
2. if the email is switched off, cancels what is waiting and does nothing else;
3. makes the mail for the latest scheduled time that has passed and has not been handled, if
   there is someone to send it to and a way to send it (otherwise the time stays due, and a
   recipient or a key added within six hours still gets it); a time that is more than six hours
   past is not mailed but recorded as skipped, with the reason;
4. sends the copies that are due, one at a time and paced, each claimed first so that no copy goes
   twice, each with its own idempotency key so that a repeat after a crash is not a second mail;
5. notes the mails that are finished, and forgets very old ones.

A copy that cannot be sent *for now* (the network, a rate limit, the provider's trouble) is tried
again after 1, 5 and 15 minutes and then every 15 minutes, until its mail is 6 hours past its time
and is given up on; one that is refused is not tried again, and what the provider said is kept next
to the recipient. Nothing in a look can raise: a failure is logged and the next look goes on.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from trafficpi.core.clock import resolve_timezone
from trafficpi.core.stop_hours import describe_hours
from trafficpi.db.mail_store import DueDelivery, MailStore
from trafficpi.db.store import Store
from trafficpi.mail.digest import (
    Digest,
    DigestInput,
    build_digest,
    camera_down_minutes,
    duration_text,
)
from trafficpi.mail.mailer import Mailer, MailMessage, SendOutcome
from trafficpi.mail.schedule import (
    DAY_MS,
    MAX_LATE_MS,
    comparison_start,
    latest_at_or_before,
    next_after,
    period_for,
)
from trafficpi.mail.settings import MailSchedule

log = logging.getLogger("trafficpi.mail")

RETRY_DELAYS_S = (60.0, 300.0, 900.0)  # after the first try, then each retry; the last repeats
PACE_S = 0.25  # between two sends: well under the provider's 10 requests a second
STUCK_AFTER_MS = 2 * 60_000  # a claim that has not been finished after this is given back
KEEP_MAILS_MS = 90 * DAY_MS  # how long a scheduled mail and its copies are remembered
PRUNE_EVERY_MS = 3_600_000
TEST_MIN_GAP_S = 5.0  # between two tests
TEST_MAX_PER_WINDOW = 10  # and no more than this many ...
TEST_WINDOW_S = 600.0  # ... in this long


class MailNotReady(Exception):
    """Something has to be set before a mail can be sent; the message says what."""


class TooManyTests(Exception):
    def __init__(self, retry_after_s: float) -> None:
        super().__init__(f"too many tests; try again in {retry_after_s:.0f} s")
        self.retry_after_s = retry_after_s


@dataclass(slots=True)
class TickResult:
    """What one look did, for the log and the tests."""

    created: int = 0  # scheduled mails made
    skipped: int = 0  # scheduled times too late to mail
    sent: int = 0  # copies sent
    failed: int = 0  # copies that ended as failures
    retried: int = 0  # copies that will be tried again
    expired: int = 0  # copies that waited too long
    cancelled: int = 0  # copies stopped because the email was switched off


@dataclass(frozen=True, slots=True)
class MailStatus:
    ready: bool  # switched on, and nothing is missing
    problems: list[str]  # what is missing, in words
    next_at: int | None  # the next scheduled time (ms), when it is on


class MailService:
    def __init__(
        self,
        store: Store,
        mail: MailStore,
        mailer: Mailer,
        *,
        now_ms: Callable[[], int] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        pace_s: float = PACE_S,
        retry_delays_s: Sequence[float] = RETRY_DELAYS_S,
    ) -> None:
        self._store = store
        self._mail = mail
        self._mailer = mailer
        self._now = now_ms or (lambda: int(time.time() * 1000))
        self._sleep = sleep
        self._pace_s = pace_s
        self._retry_delays_s = tuple(retry_delays_s)
        self._tick_lock = threading.Lock()  # one look at a time
        self._tests: deque[float] = deque()  # when the recent tests were sent, in seconds
        self._tests_lock = threading.Lock()
        self._last_prune_ms = 0

    # --- what the dashboard asks ---------------------------------------------------------------

    def status(self) -> MailStatus:
        schedule = self._mail.get_schedule()
        problems = self._problems(schedule)
        next_at = None
        if schedule.enabled:
            tz = resolve_timezone(self._store.get_setting("timezone"))
            next_at = next_after(schedule, tz, self._now())
        return MailStatus(
            ready=schedule.enabled and not problems, problems=problems, next_at=next_at
        )

    def preview(self) -> Digest:
        """The mail as it would be made now, for the past 24 hours; nothing is sent or kept."""
        now = self._now()
        return self._digest(self._mail.get_schedule(), now - DAY_MS, now, "test", now)

    def send_test(self, recipient_id: int) -> SendOutcome:
        """Send a test mail to one recipient now, and keep how it went next to them.

        ``LookupError`` if there is no such recipient, ``MailNotReady`` if there is no sender
        address, ``TooManyTests`` if tests are being sent too fast. A test that would be tried
        again later is reported as a failure: someone is waiting for the answer.
        """
        recipient = self._mail.recipient(recipient_id)
        if recipient is None:
            raise LookupError(f"no recipient {recipient_id}")
        schedule = self._mail.get_schedule()
        if not schedule.sender_address:
            raise MailNotReady("Set the sender address (and save) before sending a test.")
        self._allow_test()
        now = self._now()
        digest = self._digest(schedule, now - DAY_MS, now, "test", now)
        message = MailMessage(
            sender=schedule.sender,
            to=recipient.address,
            subject=digest.subject,
            text=digest.text,
            html=digest.html,
        )
        outcome = self._attempt(message)
        if outcome.kind == "retry":
            outcome = SendOutcome("failed", outcome.detail)
        ok = outcome.kind == "sent"
        if ok:
            log.info("daily email: a test mail to recipient %d was sent", recipient.id)
        else:
            log.warning(
                "daily email: a test mail to recipient %d did not get through: %s",
                recipient.id,
                outcome.detail,
            )
        self._mail.note_result(
            recipient.id, "test", "sent" if ok else "failed", None if ok else outcome.detail, now
        )
        return outcome

    def _allow_test(self) -> None:
        now = self._now() / 1000
        with self._tests_lock:
            while self._tests and now - self._tests[0] >= TEST_WINDOW_S:
                self._tests.popleft()
            if self._tests and now - self._tests[-1] < TEST_MIN_GAP_S:
                raise TooManyTests(TEST_MIN_GAP_S - (now - self._tests[-1]))
            if len(self._tests) >= TEST_MAX_PER_WINDOW:
                raise TooManyTests(TEST_WINDOW_S - (now - self._tests[0]))
            self._tests.append(now)

    def _problems(self, schedule: MailSchedule) -> list[str]:
        problems = []
        if not self._mail.recipients():
            problems.append("No recipient has been added.")
        if not schedule.sender_address:
            problems.append("No sender address is set.")
        if not self._mailer.ready():
            problems.append("No API key is set.")
        return problems

    # --- the look every half minute ------------------------------------------------------------

    def tick(self) -> TickResult:
        with self._tick_lock:
            result = TickResult()
            try:
                self._tick(result)
            except Exception:
                log.exception("a look at the daily email failed; the next one goes on")
            if result.sent or result.failed or result.retried or result.expired:
                log.info(
                    "daily email: %d copies sent, %d failed, %d to be tried again, %d too late",
                    result.sent,
                    result.failed,
                    result.retried,
                    result.expired,
                )
            return result

    def _tick(self, result: TickResult) -> None:
        now = self._now()
        schedule = self._mail.get_schedule()
        self._mail.release_stuck(now, older_than_ms=STUCK_AFTER_MS)
        if not schedule.enabled:
            result.cancelled = self._mail.cancel_pending()
            if result.cancelled:
                log.info("the daily email is off: %d waiting copies cancelled", result.cancelled)
            return
        result.expired = self._mail.expire_late(
            now, MAX_LATE_MS, "Not sent: too late (more than 6 hours past its time)."
        )
        self._make_the_mail_that_is_due(schedule, now, result)
        self._send_what_is_due(result)
        self._mail.close_finished_sends()
        self._forget_old(now)

    def _make_the_mail_that_is_due(
        self, schedule: MailSchedule, now: int, result: TickResult
    ) -> None:
        tz = resolve_timezone(self._store.get_setting("timezone"))
        instant = latest_at_or_before(schedule, tz, now)
        if instant is None:
            return
        handled = max(self._mail.last_scheduled_at() or 0, self._mail.enabled_since())
        if instant <= handled:
            return  # made already, or came before the email was switched on
        if self._problems(schedule):
            return  # nothing to send it to, or no way to: the time stays due while it is in reach
        start, end = period_for(schedule, tz, instant)
        late = now - instant
        if late > MAX_LATE_MS:
            note = (
                f"Not sent: the Pi was not running at the scheduled time and came up "
                f"{duration_text(late // 60_000)} late."
            )
            self._mail.add_send(
                scheduled_at=instant,
                period_start=start,
                period_end=end,
                now_ms=now,
                status="skipped",
                note=note,
                sender="",
                subject="",
                text="",
                html="",
                recipient_ids=[],
            )
            result.skipped += 1
            log.warning("daily email skipped: %s", note)
            return
        digest = self._digest(schedule, start, end, "daily", now)
        made = self._mail.add_send(
            scheduled_at=instant,
            period_start=start,
            period_end=end,
            now_ms=now,
            status="sending",
            note=None,
            sender=schedule.sender,
            subject=digest.subject,
            text=digest.text,
            html=digest.html,
            recipient_ids=[r.id for r in self._mail.recipients()],
        )
        if made is not None:
            result.created += 1
            log.info("daily email made for %s (%s)", digest.facts.timezone, digest.subject)

    def _send_what_is_due(self, result: TickResult) -> None:
        first = True
        for due in self._mail.due_deliveries(self._now()):
            if not self._mail.claim(due.id, self._now()):
                continue  # someone else has it
            if not first:
                self._sleep(self._pace_s)
            first = False
            outcome = self._attempt(
                MailMessage(
                    sender=due.sender,
                    to=due.address,
                    subject=due.subject,
                    text=due.text,
                    html=due.html,
                    idempotency_key=f"trafficpi-{due.scheduled_at}-{due.recipient_id}",
                )
            )
            self._settle(due, outcome, result)

    def _attempt(self, message: MailMessage) -> SendOutcome:
        try:
            return self._mailer.send(message)
        except Exception:  # whatever it was, the mail is not lost: it is tried again
            log.exception("sending a daily email failed unexpectedly")
            return SendOutcome("retry", "Unexpected error while sending (see the log).")

    def _settle(self, due: DueDelivery, outcome: SendOutcome, result: TickResult) -> None:
        now = self._now()
        tries = due.attempts + 1  # the claim counted this one
        if outcome.kind == "sent":
            self._mail.finish(due.id, "sent", now, provider_id=outcome.provider_id)
            self._mail.note_result(due.recipient_id, "daily", "sent", None, now)
            result.sent += 1
            return
        if outcome.kind == "retry" and self._retry_delays_s:
            delay_s = self._retry_delays_s[min(tries, len(self._retry_delays_s)) - 1]
            if outcome.retry_after_s:
                delay_s = max(delay_s, outcome.retry_after_s)
            retry_at = now + int(delay_s * 1000)
            if retry_at <= due.scheduled_at + MAX_LATE_MS:  # later than that is not sent anyway
                self._mail.finish(due.id, "retry", now, detail=outcome.detail, retry_at=retry_at)
                self._mail.note_result(due.recipient_id, "daily", "retrying", outcome.detail, now)
                result.retried += 1
                log.warning(
                    "daily email copy %d will be tried again in %.0f s: %s",
                    due.id,
                    delay_s,
                    outcome.detail,
                )
                return
        detail = outcome.detail
        if outcome.kind == "retry":
            detail = f"Gave up after {tries} {'try' if tries == 1 else 'tries'}: {detail}"
        self._mail.finish(due.id, "failed", now, detail=detail)
        self._mail.note_result(due.recipient_id, "daily", "failed", detail, now)
        result.failed += 1
        log.warning("daily email copy %d failed: %s", due.id, detail)

    def _forget_old(self, now: int) -> None:
        if now - self._last_prune_ms < PRUNE_EVERY_MS:
            return
        self._last_prune_ms = now
        gone = self._mail.delete_sends_before(now - KEEP_MAILS_MS)
        if gone:
            log.info("forgot %d daily email(s) older than 90 days", gone)

    # --- the mail itself -----------------------------------------------------------------------

    def _digest(self, schedule: MailSchedule, start: int, end: int, kind: str, now: int) -> Digest:
        """The mail for ``[start, end)``, from what the database holds."""
        store = self._store
        tz = resolve_timezone(store.get_setting("timezone"))
        stamps = [
            t
            for t in (
                store.first_stop_ms(),
                _ms(store.first_count_s()),
                _ms(store.first_health_s()),
            )
            if t is not None
        ]
        recorded_since = min(stamps) if stamps else None
        before = comparison_start(start, end)
        profile = store.get_profile()
        document = profile[0] if profile else {}
        hours = store.get_stop_hours()
        judged_hours = describe_hours(hours) if hours.limited else ""
        return build_digest(
            DigestInput(
                start_ms=start,
                end_ms=end,
                tz=tz,
                stops=store.stop_rows(start, end),
                previous_stops=(
                    store.stop_rows(before, start)
                    if recorded_since is not None and recorded_since < start
                    else None
                ),
                counts=store.minute_counts(start // 1000, end // 1000),
                has_stop_sign=bool(document.get("stop_signs")),
                down_minutes=camera_down_minutes(
                    store.health_between(start // 1000, end // 1000),
                    start,
                    end,
                    recorded_since_ms=_ms(store.first_health_s()),
                ),
                generated_ms=now,
                link_url=schedule.link_url,
                kind="test" if kind == "test" else "daily",
                units="kmh" if store.get_setting("units", "mph") == "kmh" else "mph",
                judged_hours=judged_hours,
            )
        )


def _ms(seconds: int | None) -> int | None:
    return None if seconds is None else seconds * 1000
