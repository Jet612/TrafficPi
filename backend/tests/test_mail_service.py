"""The daily email's service: what is due, who gets what once, retries, tests (plan.md D32)."""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.mail_store import MailStore
from trafficpi.db.store import Store
from trafficpi.mail.mailer import MailMessage, SendOutcome
from trafficpi.mail.service import MailNotReady, MailService, TooManyTests
from trafficpi.mail.settings import MailSchedule
from trafficpi.vision.health import HealthRow
from trafficpi.vision.rules import CountEvent, StopEvent

NY = ZoneInfo("America/New_York")
MIN = 60_000
HOUR = 60 * MIN
DAY = 24 * HOUR


def at(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=NY).timestamp() * 1000)


class Clock:
    def __init__(self, text: str) -> None:
        self.ms = at(text)

    def __call__(self) -> int:
        return self.ms

    def to(self, text: str) -> None:
        self.ms = at(text)

    def after(self, ms: int) -> None:
        self.ms += ms


class FakeMailer:
    """Records what it is asked to send and answers from a script, by address."""

    name = "fake"

    def __init__(self) -> None:
        self.sent: list[MailMessage] = []
        self.attempts: list[MailMessage] = []
        self.script: dict[str, list[SendOutcome | BaseException]] = {}
        self.is_ready = True
        self.delivered_keys: set[str] = set()

    def ready(self) -> bool:
        return self.is_ready

    def send(self, message: MailMessage) -> SendOutcome:
        self.attempts.append(message)
        scripted = self.script.get(message.to)
        if scripted:
            answer = scripted.pop(0) if len(scripted) > 1 else scripted[0]
            if isinstance(answer, BaseException):  # also a process being killed
                raise answer
            if answer.kind != "sent":
                return answer
        # like a provider that remembers an idempotency key: the same mail is sent once
        if message.idempotency_key is None or message.idempotency_key not in self.delivered_keys:
            self.sent.append(message)
        if message.idempotency_key:
            self.delivered_keys.add(message.idempotency_key)
        return SendOutcome("sent", provider_id=f"id-{len(self.sent)}")


@pytest.fixture
def store() -> Store:
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    s = Store(engine)
    s.ensure_camera()
    s.set_setting("timezone", "America/New_York")
    s.set_profile({"stop_signs": [{"id": "elm", "name": "Elm"}]})
    return s


@pytest.fixture
def mail(store: Store) -> MailStore:
    return MailStore(store)


@pytest.fixture
def clock() -> Clock:
    return Clock("2026-10-09 19:00")


@pytest.fixture
def mailer() -> FakeMailer:
    return FakeMailer()


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def service(
    store: Store, mail: MailStore, mailer: FakeMailer, clock: Clock, sleeps: list[float]
) -> MailService:
    return MailService(store, mail, mailer, now_ms=clock, sleep=sleeps.append)


SCHEDULE = MailSchedule(
    enabled=True, sender_name="TrafficPi", sender_address="report@mail.example.com"
)


def turn_on(mail: MailStore, clock: Clock, schedule: MailSchedule = SCHEDULE) -> None:
    mail.set_schedule(schedule, clock())


def recipients(mail: MailStore, *addresses: str) -> list[int]:
    return [mail.add_recipient(a, 0)[0].id for a in addresses]


def ready_to_send(mail: MailStore, clock: Clock, *addresses: str) -> list[int]:
    turn_on(mail, clock)
    return recipients(mail, *(addresses or ("owner@example.com",)))


# --- nothing happens until it should ----------------------------------------------------------


def test_nothing_is_sent_while_the_email_is_off(
    service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    recipients(mail, "owner@example.com")
    mail.set_schedule(MailSchedule(enabled=False, sender_address="report@mail.example.com"), 0)
    clock.to("2026-10-10 12:00")
    result = service.tick()
    assert mailer.attempts == [] and mail.sends() == [] and result.created == 0


def test_nothing_is_sent_before_the_time(
    service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    ready_to_send(mail, clock)
    clock.to("2026-10-09 19:59")
    service.tick()
    assert mailer.attempts == []
    clock.to("2026-10-09 20:00")
    service.tick()
    assert len(mailer.sent) == 1


def test_a_mail_goes_at_the_time_to_each_recipient_with_the_stretch_it_covers(
    service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    a, b = ready_to_send(mail, clock, "a@example.com", "b@example.com")
    clock.to("2026-10-09 20:00:20")
    result = service.tick()
    assert (result.created, result.sent, result.failed) == (1, 2, 0)
    assert [m.to for m in mailer.sent] == ["a@example.com", "b@example.com"]
    first = mailer.sent[0]
    assert first.sender == "TrafficPi <report@mail.example.com>"
    assert first.subject.startswith("TrafficPi")
    assert "Thursday 8 Oct, 8:00 pm to Friday 9 Oct, 8:00 pm" in first.text
    assert "<html" in first.html
    assert mailer.sent[0].text == mailer.sent[1].text  # the same words for everyone
    (row,) = mail.sends()
    assert row.status == "done" and (row.sent, row.pending, row.failed) == (2, 0, 0)
    for recipient_id in (a, b):
        got = mail.recipient(recipient_id)
        assert got is not None
        assert (got.last_kind, got.last_status, got.last_detail) == ("daily", "sent", None)


def test_the_same_scheduled_time_is_not_mailed_twice_however_often_it_is_looked_at(
    service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    ready_to_send(mail, clock)
    clock.to("2026-10-09 20:00:05")
    for _ in range(5):
        service.tick()
        clock.after(30_000)
    assert len(mailer.sent) == 1 and len(mail.sends()) == 1


def test_a_restart_does_not_mail_the_same_time_again(
    store: Store, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    ready_to_send(mail, clock)
    clock.to("2026-10-09 20:00:05")
    MailService(store, mail, mailer, now_ms=clock, sleep=lambda s: None).tick()
    clock.after(10 * MIN)
    MailService(store, mail, mailer, now_ms=clock, sleep=lambda s: None).tick()  # a new process
    assert len(mailer.sent) == 1


def test_the_next_day_has_its_own_mail(
    service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    ready_to_send(mail, clock)
    clock.to("2026-10-09 20:01")
    service.tick()
    clock.to("2026-10-10 20:01")
    service.tick()
    assert len(mailer.sent) == 2
    assert "Friday 9 Oct, 8:00 pm to Saturday 10 Oct, 8:00 pm" in mailer.sent[1].text
    assert mailer.sent[0].idempotency_key != mailer.sent[1].idempotency_key


def test_each_copy_has_a_key_of_its_own_so_that_a_repeat_after_a_crash_is_not_a_second_mail(
    service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    a, b = ready_to_send(mail, clock, "a@example.com", "b@example.com")
    clock.to("2026-10-09 20:01")
    service.tick()
    first, second = mailer.sent
    assert first.idempotency_key == f"trafficpi-{at('2026-10-09 20:00')}-{a}"
    assert second.idempotency_key == f"trafficpi-{at('2026-10-09 20:00')}-{b}"
    # the next day there is a third recipient, and the numbers of the copies (3, 4, 5) are no longer
    # the numbers of the recipients (1, 2, 3): the key follows the recipient, not the copy
    (c,) = recipients(mail, "c@example.com")
    clock.to("2026-10-10 20:01")
    service.tick()
    keys = {m.to: m.idempotency_key for m in mailer.sent[2:]}
    assert keys == {
        "a@example.com": f"trafficpi-{at('2026-10-10 20:00')}-{a}",
        "b@example.com": f"trafficpi-{at('2026-10-10 20:00')}-{b}",
        "c@example.com": f"trafficpi-{at('2026-10-10 20:00')}-{c}",
    }
    assert c == 3


def test_only_the_chosen_days_have_a_mail_and_monday_covers_the_weekend(
    service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    turn_on(mail, clock, MailSchedule(enabled=True, days=(0, 1, 2, 3, 4), sender_address="a@b.io"))
    recipients(mail, "owner@example.com")
    clock.to("2026-10-10 20:01")  # a Saturday
    service.tick()
    clock.to("2026-10-11 20:01")  # a Sunday
    service.tick()
    assert mailer.sent == []
    clock.to("2026-10-12 20:01")  # Monday
    service.tick()
    assert len(mailer.sent) == 1
    assert "Friday 9 Oct, 8:00 pm to Monday 12 Oct, 8:00 pm" in mailer.sent[0].text


def test_a_change_of_the_time_takes_effect_at_the_next_look(
    service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    ready_to_send(mail, clock)
    mail.set_schedule(
        MailSchedule(enabled=True, time="07:30", sender_address="report@mail.example.com"),
        clock(),
    )
    clock.to("2026-10-09 20:30")
    service.tick()
    assert mailer.sent == []  # 07:30 this morning came before it was switched on
    clock.to("2026-10-10 07:31")
    service.tick()
    assert len(mailer.sent) == 1


# --- when it was switched on ------------------------------------------------------------------


def test_switching_it_on_after_todays_time_does_not_send_todays_mail(
    service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    clock.to("2026-10-09 21:00")
    ready_to_send(mail, clock)
    clock.to("2026-10-09 21:05")
    service.tick()
    assert mailer.sent == [] and mail.sends() == []
    clock.to("2026-10-10 20:00:30")
    service.tick()
    assert len(mailer.sent) == 1


# --- when the Pi was not running --------------------------------------------------------------


def test_a_mail_a_few_hours_late_is_still_sent(
    service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    ready_to_send(mail, clock)
    clock.to("2026-10-09 23:30")  # the Pi came up three and a half hours late
    service.tick()
    assert len(mailer.sent) == 1


def test_a_mail_more_than_six_hours_late_is_skipped_and_the_reason_kept(
    service: MailService,
    mail: MailStore,
    mailer: FakeMailer,
    clock: Clock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ready_to_send(mail, clock)
    clock.to("2026-10-10 03:10")  # seven hours and ten minutes after 20:00
    with caplog.at_level(logging.WARNING, logger="trafficpi.mail"):
        result = service.tick()
    assert mailer.attempts == [] and result.skipped == 1 and result.created == 0
    (row,) = mail.sends()
    assert row.status == "skipped"
    assert "7 hours 10 minutes" in (row.note or "") and "not running" in (row.note or "")
    assert any("skipped" in r.message for r in caplog.records)
    service.tick()
    assert len(mail.sends()) == 1  # said once


def test_exactly_six_hours_late_is_still_in_time_and_one_minute_more_is_not(
    service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    ready_to_send(mail, clock)
    clock.to("2026-10-10 02:00")  # exactly six hours after 20:00
    service.tick()
    assert len(mailer.sent) == 1
    clock.to("2026-10-11 02:01")  # six hours and a minute after the next time, 20:00 on the 10th
    service.tick()
    assert len(mailer.sent) == 1 and mail.sends()[0].status == "skipped"


def test_after_a_long_outage_only_the_latest_time_is_looked_at(
    service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    ready_to_send(mail, clock)
    clock.to("2026-10-13 20:30")  # four days later, half an hour after the time
    service.tick()
    assert len(mailer.sent) == 1
    assert "Monday 12 Oct, 8:00 pm to Tuesday 13 Oct, 8:00 pm" in mailer.sent[0].text
    assert len(mail.sends()) == 1  # the days in between are not each reported as missed


def test_a_copy_still_waiting_more_than_six_hours_after_its_time_is_not_sent_late(
    service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    (recipient,) = ready_to_send(mail, clock)
    mailer.script["owner@example.com"] = [SendOutcome("retry", "no network")]
    clock.to("2026-10-09 20:01")
    service.tick()  # the first try failed; it waits to try again
    clock.to("2026-10-10 02:30")  # and the Pi was off, and has come up 6.5 hours later
    mailer.script.clear()
    result = service.tick()
    assert result.expired == 1 and mailer.sent == []
    got = mail.recipient(recipient)
    assert (
        got is not None
        and got.last_status == "failed"
        and "too late" in (got.last_detail or "").lower()
    )


# --- not ready --------------------------------------------------------------------------------


def test_with_no_recipient_nothing_is_made_and_one_added_in_time_gets_the_mail(
    service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    turn_on(mail, clock)
    clock.to("2026-10-09 20:01")
    service.tick()
    assert mail.sends() == []
    clock.to("2026-10-09 21:00")
    recipients(mail, "late-to-the-list@example.com")
    service.tick()
    assert [m.to for m in mailer.sent] == ["late-to-the-list@example.com"]


def test_without_a_key_nothing_is_made_and_a_key_set_in_time_gets_the_mail(
    service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    ready_to_send(mail, clock)
    mailer.is_ready = False
    clock.to("2026-10-09 20:01")
    service.tick()
    assert mail.sends() == [] and mailer.attempts == []
    mailer.is_ready = True  # the owner pastes the key at 21:15, when DNS has propagated
    clock.to("2026-10-09 21:15")
    service.tick()
    assert len(mailer.sent) == 1


def test_what_is_missing_is_told_in_words(
    service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    status = service.status()
    assert status.ready is False and status.next_at is None
    assert any("recipient" in p.lower() for p in status.problems)
    assert any("sender" in p.lower() for p in status.problems)
    mailer.is_ready = False
    assert any("key" in p.lower() for p in service.status().problems)
    ready_to_send(mail, clock)
    mailer.is_ready = True
    status = service.status()
    assert status.problems == [] and status.ready is True
    assert status.next_at == at("2026-10-09 20:00")


def test_an_email_that_is_off_is_not_ready_though_nothing_is_missing(
    service: MailService, mail: MailStore, clock: Clock
) -> None:
    recipients(mail, "owner@example.com")
    mail.set_schedule(MailSchedule(enabled=False, sender_address="report@mail.example.com"), 0)
    status = service.status()
    assert status.problems == [] and status.ready is False and status.next_at is None


# --- a copy that does not get through ---------------------------------------------------------


def test_one_that_is_refused_fails_alone_with_the_reason_and_is_not_tried_again(
    service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    bad, good = ready_to_send(mail, clock, "bad@example.com", "good@example.com")
    mailer.script["bad@example.com"] = [SendOutcome("failed", "Resend refused it (422): no")]
    clock.to("2026-10-09 20:01")
    result = service.tick()
    assert (result.sent, result.failed) == (1, 1)
    assert [m.to for m in mailer.sent] == ["good@example.com"]
    clock.to("2026-10-09 22:00")
    service.tick()
    assert len([m for m in mailer.attempts if m.to == "bad@example.com"]) == 1
    row = mail.recipient(bad)
    assert row is not None and row.last_status == "failed" and "422" in (row.last_detail or "")
    row = mail.recipient(good)
    assert row is not None and row.last_status == "sent"
    (send,) = mail.sends()
    assert (send.sent, send.failed, send.status) == (1, 1, "done")


def test_a_network_failure_is_tried_again_after_one_five_and_fifteen_minutes_and_then_given_up(
    service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    (recipient,) = ready_to_send(mail, clock)
    mailer.script["owner@example.com"] = [SendOutcome("retry", "Could not reach Resend")]
    clock.to("2026-10-09 20:00:10")
    start = clock()
    service.tick()
    assert len(mailer.attempts) == 1
    row = mail.recipient(recipient)
    assert row is not None and row.last_status == "retrying"

    def at_offset(ms: int) -> None:
        clock.ms = start + ms
        service.tick()

    at_offset(59_000)
    assert len(mailer.attempts) == 1  # not yet
    at_offset(61_000)
    assert len(mailer.attempts) == 2
    at_offset(61_000 + 299_000)
    assert len(mailer.attempts) == 2
    at_offset(61_000 + 301_000)
    assert len(mailer.attempts) == 3
    at_offset(61_000 + 301_000 + 899_000)
    assert len(mailer.attempts) == 3
    at_offset(61_000 + 301_000 + 901_000)
    assert len(mailer.attempts) == 4  # the first and three more
    row = mail.recipient(recipient)
    assert row is not None and row.last_status == "failed"
    assert "4 tries" in (row.last_detail or "") and "Could not reach Resend" in (
        row.last_detail or ""
    )
    at_offset(3 * HOUR)
    assert len(mailer.attempts) == 4  # and no more
    assert mail.sends()[0].status == "done"


def test_a_try_again_that_works_ends_as_sent_and_says_so(
    service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    (recipient,) = ready_to_send(mail, clock)
    mailer.script["owner@example.com"] = [
        SendOutcome("retry", "Resend is busy"),
        SendOutcome("sent"),
    ]
    clock.to("2026-10-09 20:00:10")
    service.tick()
    clock.after(61_000)
    service.tick()
    assert len(mailer.sent) == 1 and len(mailer.attempts) == 2
    row = mail.recipient(recipient)
    assert row is not None and row.last_status == "sent" and row.last_detail is None


def test_what_the_provider_asks_for_is_waited_if_it_is_longer_than_our_own_delay(
    service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    ready_to_send(mail, clock)
    mailer.script["owner@example.com"] = [SendOutcome("retry", "slow down", retry_after_s=180.0)]
    clock.to("2026-10-09 20:00:10")
    start = clock()
    service.tick()
    clock.ms = start + 90_000  # our own delay (a minute) has passed, the provider's has not
    service.tick()
    assert len(mailer.attempts) == 1
    clock.ms = start + 181_000
    service.tick()
    assert len(mailer.attempts) == 2


def test_an_error_nobody_expected_is_a_try_again_and_never_stops_the_look(
    service: MailService,
    mail: MailStore,
    mailer: FakeMailer,
    clock: Clock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    a, _b = ready_to_send(mail, clock, "a@example.com", "b@example.com")
    mailer.script["a@example.com"] = [RuntimeError("something odd")]
    clock.to("2026-10-09 20:00:10")
    with caplog.at_level(logging.ERROR, logger="trafficpi.mail"):
        result = service.tick()
    assert [m.to for m in mailer.sent] == ["b@example.com"]  # the other went out
    assert result.retried == 1
    row = mail.recipient(a)
    assert (
        row is not None
        and row.last_status == "retrying"
        and "unexpected" in (row.last_detail or "").lower()
    )
    assert any("something odd" in (r.exc_text or "") for r in caplog.records)


def test_what_a_look_did_is_said_in_the_journal(
    service: MailService,
    mail: MailStore,
    mailer: FakeMailer,
    clock: Clock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ready_to_send(mail, clock, "a@example.com", "b@example.com")
    mailer.script["b@example.com"] = [SendOutcome("failed", "refused")]
    clock.to("2026-10-09 20:00:10")
    with caplog.at_level(logging.INFO, logger="trafficpi.mail"):
        service.tick()
        service.tick()  # nothing happened this time, so nothing is said
    said = [r.message for r in caplog.records if "copies sent" in r.message]
    assert said == ["daily email: 1 copies sent, 1 failed, 0 to be tried again, 0 too late"]


def test_the_copies_are_paced_so_the_provider_is_not_hurried(
    service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock, sleeps: list[float]
) -> None:
    ready_to_send(mail, clock, "a@example.com", "b@example.com", "c@example.com")
    clock.to("2026-10-09 20:00:10")
    service.tick()
    assert len(mailer.sent) == 3
    assert len(sleeps) == 2 and all(0.1 <= s <= 1.0 for s in sleeps)  # between them, not before


# --- a crash in the middle --------------------------------------------------------------------


def test_a_crash_in_the_middle_of_a_send_gives_everyone_their_mail_once(
    store: Store, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    """The process dies while the second copy is on its way. After a restart the first is not sent
    again, the second is sent again with the same key (the provider drops the repeat if the first
    try got through) and the third is sent: each gets one."""
    ready_to_send(mail, clock, "a@example.com", "b@example.com", "c@example.com")
    mailer.script["b@example.com"] = [KeyboardInterrupt()]  # the process is killed during the call
    clock.to("2026-10-09 20:00:10")
    first_process = MailService(store, mail, mailer, now_ms=clock, sleep=lambda s: None)
    with pytest.raises(KeyboardInterrupt):
        first_process.tick()
    assert [m.to for m in mailer.sent] == ["a@example.com"]
    mailer.script.clear()
    clock.after(30_000)
    second_process = MailService(store, mail, mailer, now_ms=clock, sleep=lambda s: None)
    second_process.tick()
    assert [m.to for m in mailer.sent] == ["a@example.com", "c@example.com"]  # b is held: claimed
    clock.after(3 * MIN)  # the claim that was never finished is given back
    second_process.tick()
    assert sorted(m.to for m in mailer.sent) == ["a@example.com", "b@example.com", "c@example.com"]
    keys = [m.idempotency_key for m in mailer.attempts if m.to == "b@example.com"]
    assert len(keys) == 2 and keys[0] == keys[1]  # the same key, so a repeat is not a second mail


# --- changes while mails wait -----------------------------------------------------------------


def test_switching_it_off_cancels_what_has_not_gone_out(
    service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    ready_to_send(mail, clock)
    mailer.script["owner@example.com"] = [SendOutcome("retry", "no network")]
    clock.to("2026-10-09 20:00:10")
    service.tick()
    mail.set_schedule(
        MailSchedule(enabled=False, sender_address="report@mail.example.com"), clock()
    )
    clock.after(2 * MIN)
    mailer.script.clear()
    result = service.tick()
    assert result.cancelled == 1 and mailer.sent == []


def test_a_recipient_taken_off_the_list_before_their_copy_goes_gets_nothing(
    service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    a, _b = ready_to_send(mail, clock, "a@example.com", "b@example.com")
    mailer.script["a@example.com"] = [SendOutcome("retry", "no network")]
    clock.to("2026-10-09 20:00:10")
    service.tick()
    mail.remove_recipient(a)
    clock.after(2 * MIN)
    mailer.script.clear()
    service.tick()
    assert [m.to for m in mailer.sent] == ["b@example.com"]


def test_a_recipient_added_after_the_mail_was_made_does_not_get_that_mail(
    service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    ready_to_send(mail, clock, "a@example.com")
    clock.to("2026-10-09 20:00:10")
    service.tick()
    recipients(mail, "new@example.com")
    clock.after(10 * MIN)
    service.tick()
    assert [m.to for m in mailer.sent] == ["a@example.com"]
    clock.to("2026-10-10 20:00:10")
    service.tick()
    assert [m.to for m in mailer.sent] == ["a@example.com", "a@example.com", "new@example.com"]


def test_old_mails_are_forgotten_after_three_months(
    service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    ready_to_send(mail, clock)
    clock.to("2026-10-09 20:00:10")
    service.tick()
    assert len(mail.sends()) == 1
    clock.to("2027-01-20 12:00")  # a hundred days on: the first mail is old, yesterday's was missed
    service.tick()
    (left,) = mail.sends()
    assert left.scheduled_at == at("2027-01-19 20:00") and left.status == "skipped"


# --- what the mail says is what the data says -------------------------------------------------


def test_the_mail_is_made_from_the_passes_the_counts_and_the_camera_of_its_stretch(
    store: Store, service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    ready_to_send(mail, clock)
    in_period = at("2026-10-09 08:15")
    for i, outcome in enumerate(["did_not_stop", "did_not_stop", "rolling", "stopped", "stopped"]):
        store.record_stop(
            StopEvent(0, i, 2, "car", "elm", outcome, hold_s=1.2, min_speed=0.1, observed_s=3.0),  # type: ignore[arg-type]
            in_period + i * 1_000,
        )
    store.record_stop(  # the day before: the comparison
        StopEvent(
            0, 99, 2, "car", "elm", "did_not_stop", hold_s=0.0, min_speed=2.0, observed_s=3.0
        ),  # type: ignore[arg-type]
        at("2026-10-08 12:00"),
    )
    store.record_stop(  # before the stretch began: in neither
        StopEvent(
            0, 98, 2, "car", "elm", "did_not_stop", hold_s=0.0, min_speed=2.0, observed_s=3.0
        ),  # type: ignore[arg-type]
        at("2026-10-06 12:00"),
    )
    for i in range(3):
        store.record_count(CountEvent(0, i, 2, "car", "main", 1), in_period + i * 1_000)
    # the camera was recorded all day but for two hours in the afternoon
    store.add_health(
        [
            HealthRow(m * 60, 60.0, 20.0, 30.0, 29.0, 0, 12, 12)
            for m in range(at("2026-10-08 12:00") // MIN, at("2026-10-09 20:00") // MIN)
            if not at("2026-10-09 13:00") // MIN <= m < at("2026-10-09 15:00") // MIN
        ]
    )
    clock.to("2026-10-09 20:00:20")
    service.tick()
    (message,) = mailer.sent
    assert "2 vehicles did not stop" in message.text
    assert "Before: 1" in message.text
    assert "Rolled through:" in message.text and "Stopped:" in message.text
    assert "Did not stop:" in message.text and "(40%)" in message.text  # 2 of the 5 judged
    assert "Vehicles counted:  3" in message.text
    assert "not counting for about 2 hours" in message.text


def test_with_no_stop_sign_in_the_profile_the_mail_says_so(
    store: Store, service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    store.set_profile({"lines": []})
    ready_to_send(mail, clock)
    clock.to("2026-10-09 20:00:20")
    service.tick()
    assert "No stop sign is set up" in mailer.sent[0].text


def test_nothing_is_compared_with_a_time_before_anything_was_recorded(
    service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    ready_to_send(mail, clock)
    clock.to("2026-10-09 20:00:20")
    service.tick()
    assert "Before" not in mailer.sent[0].text and "earlier" in mailer.sent[0].text


def test_the_link_in_the_mail_is_the_one_in_the_settings(
    service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    turn_on(
        mail,
        clock,
        MailSchedule(
            enabled=True, sender_address="report@mail.example.com", link_url="https://t.example.com"
        ),
    )
    recipients(mail, "owner@example.com")
    clock.to("2026-10-09 20:00:20")
    service.tick()
    assert "https://t.example.com" in mailer.sent[0].text


# --- tests and previews -----------------------------------------------------------------------


def test_a_test_goes_to_the_one_recipient_at_once_and_says_it_is_one(
    service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    a, _ = ready_to_send(mail, clock, "a@example.com", "b@example.com")
    outcome = service.send_test(a)
    assert outcome.kind == "sent"
    (message,) = mailer.sent
    assert message.to == "a@example.com" and message.subject.startswith("[Test] ")
    assert message.idempotency_key is None  # a test is meant to arrive each time it is pressed
    row = mail.recipient(a)
    assert row is not None and (row.last_kind, row.last_status) == ("test", "sent")
    assert mail.sends() == []  # a test is not a scheduled mail


def test_a_test_that_fails_says_why_to_the_one_who_pressed_it_and_next_to_the_recipient(
    service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    (a,) = ready_to_send(mail, clock)
    mailer.script["owner@example.com"] = [SendOutcome("failed", "Resend refused it (403): domain")]
    outcome = service.send_test(a)
    assert outcome.kind == "failed" and "403" in outcome.detail
    row = mail.recipient(a)
    assert row is not None and row.last_status == "failed" and "403" in (row.last_detail or "")


def test_a_test_that_would_be_tried_again_is_told_as_a_failure_because_nobody_is_waiting(
    service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    (a,) = ready_to_send(mail, clock)
    mailer.script["owner@example.com"] = [SendOutcome("retry", "Could not reach Resend")]
    outcome = service.send_test(a)
    assert outcome.kind == "failed" and "Could not reach Resend" in outcome.detail
    assert len(mailer.attempts) == 1
    row = mail.recipient(a)
    assert row is not None and row.last_status == "failed"


def test_a_test_for_someone_who_is_not_on_the_list_is_refused(
    service: MailService, mail: MailStore, clock: Clock
) -> None:
    ready_to_send(mail, clock)
    with pytest.raises(LookupError):
        service.send_test(9999)


def test_a_test_needs_a_sender_address(
    service: MailService, mail: MailStore, mailer: FakeMailer
) -> None:
    (a,) = recipients(mail, "owner@example.com")
    with pytest.raises(MailNotReady, match="sender"):
        service.send_test(a)
    assert mailer.attempts == []


def test_a_test_may_be_sent_while_the_email_is_still_off(
    service: MailService, mail: MailStore, mailer: FakeMailer
) -> None:
    mail.set_schedule(MailSchedule(enabled=False, sender_address="report@mail.example.com"), 0)
    (a,) = recipients(mail, "owner@example.com")
    assert service.send_test(a).kind == "sent"


def test_tests_are_limited_to_one_every_few_seconds_and_a_few_in_ten_minutes(
    service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    (a,) = ready_to_send(mail, clock)
    service.send_test(a)
    clock.after(2_000)
    with pytest.raises(TooManyTests) as raised:
        service.send_test(a)
    assert 2 <= raised.value.retry_after_s <= 5
    clock.after(4_000)
    service.send_test(a)  # enough time has gone
    sent = 2
    while sent < 10:
        clock.after(6_000)
        service.send_test(a)
        sent += 1
    clock.after(6_000)
    with pytest.raises(TooManyTests) as raised:
        service.send_test(a)  # ten in under ten minutes
    assert raised.value.retry_after_s > 60
    clock.after(11 * MIN)
    service.send_test(a)  # the window has moved on
    assert len(mailer.sent) == 11


def test_a_refused_test_costs_nothing_and_sends_nothing(
    service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    (a,) = ready_to_send(mail, clock)
    service.send_test(a)
    clock.after(1_000)
    with pytest.raises(TooManyTests):
        service.send_test(a)
    assert len(mailer.attempts) == 1


def test_the_preview_is_the_mail_as_it_would_be_made_now_and_sends_nothing(
    service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    ready_to_send(mail, clock)
    digest = service.preview()
    assert digest.subject.startswith("[Test] ")
    assert "Thursday 8 Oct, 7:00 pm to Friday 9 Oct, 7:00 pm" in digest.text
    assert mailer.attempts == [] and mail.sends() == []


def test_the_fastest_estimate_in_the_mail_is_in_the_units_the_owner_chose(
    store: Store, service: MailService, mail: MailStore, mailer: FakeMailer, clock: Clock
) -> None:
    def fast_pass(when: str) -> None:
        store.record_stop(
            StopEvent(
                0, 1, 2, "car", "elm", "did_not_stop",  # type: ignore[arg-type]
                hold_s=0.0, min_speed=2.0, observed_s=3.0, max_speed=5.4,
            ),
            at(when),
        )  # fmt: skip

    ready_to_send(mail, clock)
    fast_pass("2026-10-09 17:42")  # 5.4 sizes a second of a car: 13.5 m/s
    clock.to("2026-10-09 20:00:20")
    service.tick()
    assert "about 30 mph (a car, Fri 9 Oct 5:42 pm)" in mailer.sent[0].text
    store.set_setting("units", "kmh")  # the owner changes the setting; the next mail follows it
    fast_pass("2026-10-10 12:00")
    clock.to("2026-10-10 20:00:20")
    service.tick()
    assert "about 50 km/h (a car, Sat 10 Oct 12:00 pm)" in mailer.sent[1].text
