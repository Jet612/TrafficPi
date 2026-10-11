"""The daily email's tables: settings, recipients, each scheduled mail and each copy."""

import pytest
from sqlalchemy import text

from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.mail_store import MailLimitReached, MailStore
from trafficpi.db.store import Store
from trafficpi.mail.settings import ALL_DAYS, MAX_RECIPIENTS, MailSchedule

NOW = 1_760_000_000_000
MIN = 60_000
HOUR = 60 * MIN


@pytest.fixture
def store() -> Store:
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    s = Store(engine)
    s.ensure_camera()
    return s


@pytest.fixture
def mail(store: Store) -> MailStore:
    return MailStore(store)


def send(mail: MailStore, scheduled_at: int, recipients: list[int], **over: object) -> int | None:
    fields: dict[str, object] = {
        "scheduled_at": scheduled_at,
        "period_start": scheduled_at - 24 * HOUR,
        "period_end": scheduled_at,
        "now_ms": scheduled_at + 5_000,
        "status": "sending",
        "note": None,
        "sender": "TrafficPi <report@mail.example.com>",
        "subject": "the subject",
        "text": "the words",
        "html": "<p>the words</p>",
        "recipient_ids": recipients,
        **over,
    }
    return mail.add_send(**fields)  # type: ignore[arg-type]


def add(mail: MailStore, address: str = "a@example.com") -> int:
    row, _ = mail.add_recipient(address, NOW)
    return row.id


# --- the schedule -----------------------------------------------------------------------------


def test_the_schedule_starts_off_at_eight_every_day(mail: MailStore) -> None:
    assert mail.get_schedule() == MailSchedule()
    assert mail.enabled_since() == 0


def test_the_schedule_is_kept_and_read_back(mail: MailStore) -> None:
    schedule = MailSchedule(
        enabled=True,
        time="07:30",
        days=(0, 2, 4),
        sender_name="Elm Street cam",
        sender_address="report@mail.example.com",
        link_url="https://traffic.example.com",
    )
    mail.set_schedule(schedule, NOW)
    assert mail.get_schedule() == schedule


def test_turning_it_on_notes_when_and_changing_other_things_does_not_move_that(
    mail: MailStore,
) -> None:
    off = MailSchedule(sender_address="a@example.com")
    on = MailSchedule(enabled=True, sender_address="a@example.com")
    mail.set_schedule(off, NOW)
    assert mail.enabled_since() == 0
    mail.set_schedule(on, NOW + 1_000)
    assert mail.enabled_since() == NOW + 1_000
    mail.set_schedule(
        MailSchedule(enabled=True, time="09:00", sender_address="a@example.com"), NOW + 9_000
    )
    assert mail.enabled_since() == NOW + 1_000  # still the same switching on
    mail.set_schedule(off, NOW + 20_000)
    mail.set_schedule(on, NOW + 30_000)
    assert mail.enabled_since() == NOW + 30_000  # off and on again is a new beginning


def test_a_stored_schedule_that_makes_no_sense_is_read_as_the_safe_thing(
    store: Store, mail: MailStore
) -> None:
    for key, value in [
        ("mail_enabled", "yes"),
        ("mail_time", "25:99"),
        ("mail_days", [9, 9]),
        ("mail_sender_name", 5),
        ("mail_sender_address", "not an address"),
        ("mail_link_url", "javascript:alert(1)"),
    ]:
        store.set_setting(key, value)
    got = mail.get_schedule()
    assert got == MailSchedule()  # off, 20:00, every day, no address, no link


def test_only_a_plain_true_turns_the_email_on(store: Store, mail: MailStore) -> None:
    store.set_setting("mail_sender_address", "a@example.com")
    for value in ("true", 1, "yes", None, [True]):
        store.set_setting("mail_enabled", value)
        assert mail.get_schedule().enabled is False, value
    store.set_setting("mail_enabled", True)
    assert mail.get_schedule().enabled is True


def test_an_on_schedule_with_a_ruined_sender_address_is_read_as_off(
    store: Store, mail: MailStore
) -> None:
    store.set_setting("mail_enabled", True)
    store.set_setting("mail_sender_address", "ruined")
    got = mail.get_schedule()
    assert got.enabled is False and got.sender_address == ""


# --- the recipients ---------------------------------------------------------------------------


def test_recipients_are_listed_in_the_order_they_were_added(mail: MailStore) -> None:
    mail.add_recipient("b@example.com", NOW)
    mail.add_recipient("a@example.com", NOW + 1)
    assert [r.address for r in mail.recipients()] == ["b@example.com", "a@example.com"]
    first = mail.recipients()[0]
    assert first.added_at == NOW and first.last_at is None and first.last_status is None


def test_an_address_added_twice_is_added_once_and_the_second_time_says_so(mail: MailStore) -> None:
    row, created = mail.add_recipient("a@example.com", NOW)
    assert created is True
    again, created_again = mail.add_recipient("a@example.com", NOW + 5)
    assert created_again is False and again.id == row.id and again.added_at == NOW
    assert len(mail.recipients()) == 1


def test_the_list_holds_twenty_and_a_twenty_first_is_refused(mail: MailStore) -> None:
    for i in range(MAX_RECIPIENTS):
        mail.add_recipient(f"person{i}@example.com", NOW + i)
    with pytest.raises(MailLimitReached):
        mail.add_recipient("one-too-many@example.com", NOW)
    assert len(mail.recipients()) == MAX_RECIPIENTS
    # one that is there already is not "one more"
    _, created = mail.add_recipient("person3@example.com", NOW)
    assert created is False


def test_a_recipient_can_be_removed_once(mail: MailStore) -> None:
    recipient = add(mail)
    assert mail.remove_recipient(recipient) is True
    assert mail.remove_recipient(recipient) is False
    assert mail.recipients() == [] and mail.recipient(recipient) is None


def test_removing_a_recipient_takes_their_copies_with_them_and_not_the_mail(
    store: Store, mail: MailStore
) -> None:
    a, b = add(mail, "a@example.com"), add(mail, "b@example.com")
    send_id = send(mail, NOW, [a, b])
    assert send_id is not None
    mail.remove_recipient(a)
    with store.transaction() as s:
        left = s.execute(text("SELECT recipient_id FROM mail_deliveries")).scalars().all()
        kept = s.execute(text("SELECT count(*) FROM mail_sends")).scalar_one()
    assert left == [b] and kept == 1


def test_what_happened_to_the_last_try_is_kept_with_the_recipient(mail: MailStore) -> None:
    recipient = add(mail)
    mail.note_result(recipient, "test", "failed", "The domain is not verified", NOW + 10)
    row = mail.recipient(recipient)
    assert row is not None
    assert (row.last_at, row.last_kind, row.last_status) == (NOW + 10, "test", "failed")
    assert row.last_detail == "The domain is not verified"
    mail.note_result(recipient, "daily", "sent", None, NOW + 20)
    row = mail.recipient(recipient)
    assert row is not None
    assert (row.last_kind, row.last_status, row.last_detail) == ("daily", "sent", None)


# --- each scheduled mail ----------------------------------------------------------------------


def test_a_scheduled_mail_is_made_once_with_a_copy_for_each_recipient(mail: MailStore) -> None:
    a, b = add(mail, "a@example.com"), add(mail, "b@example.com")
    assert mail.last_scheduled_at() is None
    first = send(mail, NOW, [a, b])
    assert first is not None
    assert mail.last_scheduled_at() == NOW
    assert send(mail, NOW, [a, b]) is None  # the same instant again makes nothing
    (row,) = mail.sends()
    assert (row.scheduled_at, row.status, row.pending, row.sent, row.failed) == (
        NOW,
        "sending",
        2,
        0,
        0,
    )
    assert row.period_end - row.period_start == 24 * HOUR and row.subject == "the subject"


def test_a_skipped_mail_is_a_row_with_the_reason_and_no_copies(mail: MailStore) -> None:
    send(
        mail,
        NOW,
        [],
        status="skipped",
        note="The Pi was not running at the time (7 hours late)",
        sender="",
        subject="",
        text="",
        html="",
    )
    (row,) = mail.sends()
    assert row.status == "skipped" and "7 hours late" in (row.note or "")
    assert (row.pending, row.sent, row.failed) == (0, 0, 0)
    assert mail.last_scheduled_at() == NOW


def test_the_latest_scheduled_instant_is_the_biggest_not_the_last_written(mail: MailStore) -> None:
    send(mail, NOW + 2 * HOUR, [])
    send(mail, NOW, [])
    assert mail.last_scheduled_at() == NOW + 2 * HOUR


def test_the_list_of_sends_is_the_newest_first_and_limited(mail: MailStore) -> None:
    for i in range(5):
        send(mail, NOW + i * 24 * HOUR, [])
    assert [s.scheduled_at for s in mail.sends(limit=3)] == [
        NOW + 4 * 24 * HOUR,
        NOW + 3 * 24 * HOUR,
        NOW + 2 * 24 * HOUR,
    ]


# --- the copies -------------------------------------------------------------------------------


def due(mail: MailStore, now: int) -> list[int]:
    return [d.id for d in mail.due_deliveries(now)]


def test_a_copy_is_due_from_the_moment_it_is_made_and_carries_the_mail_and_the_address(
    mail: MailStore,
) -> None:
    a = add(mail, "a@example.com")
    send(mail, NOW, [a])
    (one,) = mail.due_deliveries(NOW + 5_000)
    assert one.address == "a@example.com" and one.recipient_id == a
    assert (one.attempts, one.scheduled_at) == (0, NOW)
    assert one.sender == "TrafficPi <report@mail.example.com>"
    assert (one.subject, one.text, one.html) == ("the subject", "the words", "<p>the words</p>")


def test_a_copy_is_claimed_by_one_taker_only(mail: MailStore) -> None:
    send(mail, NOW, [add(mail)])
    (one,) = mail.due_deliveries(NOW + 5_000)
    assert mail.claim(one.id, NOW + 6_000) is True
    assert mail.claim(one.id, NOW + 6_001) is False  # someone has it
    assert due(mail, NOW + 7_000) == []  # and it is no longer on the list of what is due


def test_a_claim_counts_an_attempt(mail: MailStore) -> None:
    send(mail, NOW, [add(mail)])
    (one,) = mail.due_deliveries(NOW + 5_000)
    mail.claim(one.id, NOW + 6_000)
    mail.finish(one.id, "retry", NOW + 7_000, detail="try later", retry_at=NOW + 7_000 + MIN)
    (again,) = mail.due_deliveries(NOW + 7_000 + MIN)
    assert again.attempts == 1


def test_a_sent_copy_is_sent_once_and_not_due_again(mail: MailStore) -> None:
    send(mail, NOW, [add(mail)])
    (one,) = mail.due_deliveries(NOW + 5_000)
    mail.claim(one.id, NOW + 6_000)
    assert mail.finish(one.id, "sent", NOW + 7_000, provider_id="abc") is True
    assert mail.finish(one.id, "sent", NOW + 8_000, provider_id="def") is False  # already finished
    assert due(mail, NOW + 10 * HOUR) == []
    (row,) = mail.sends()
    assert (row.sent, row.pending, row.failed) == (1, 0, 0)


def test_a_failed_copy_keeps_the_reason_and_is_not_tried_again(mail: MailStore) -> None:
    send(mail, NOW, [add(mail)])
    (one,) = mail.due_deliveries(NOW + 5_000)
    mail.claim(one.id, NOW + 6_000)
    mail.finish(one.id, "failed", NOW + 7_000, detail="the domain is not verified")
    assert due(mail, NOW + 10 * HOUR) == []
    (row,) = mail.sends()
    assert (row.failed, row.pending) == (1, 0)


def test_a_copy_to_try_again_waits_for_its_time(mail: MailStore) -> None:
    send(mail, NOW, [add(mail)])
    (one,) = mail.due_deliveries(NOW + 5_000)
    mail.claim(one.id, NOW + 6_000)
    mail.finish(one.id, "retry", NOW + 7_000, detail="no network", retry_at=NOW + 7_000 + 5 * MIN)
    assert due(mail, NOW + 7_000 + 5 * MIN - 1) == []
    assert due(mail, NOW + 7_000 + 5 * MIN) == [one.id]


def test_only_a_claimed_copy_can_be_finished(mail: MailStore) -> None:
    send(mail, NOW, [add(mail)])
    (one,) = mail.due_deliveries(NOW + 5_000)
    assert mail.finish(one.id, "sent", NOW + 6_000) is False  # nobody claimed it
    assert due(mail, NOW + 6_000) == [one.id]


def test_a_copy_that_was_claimed_by_a_send_that_died_is_given_back(mail: MailStore) -> None:
    send(mail, NOW, [add(mail)])
    (one,) = mail.due_deliveries(NOW + 5_000)
    mail.claim(one.id, NOW + 6_000)
    assert mail.release_stuck(NOW + 6_000 + MIN, older_than_ms=2 * MIN) == 0  # a minute is not long
    assert mail.release_stuck(NOW + 6_000 + 3 * MIN, older_than_ms=2 * MIN) == 1
    (again,) = mail.due_deliveries(NOW + 6_000 + 3 * MIN)
    assert again.id == one.id and again.attempts == 1  # the attempt that was made still counts


def test_switching_the_email_off_cancels_what_has_not_gone_out(mail: MailStore) -> None:
    a, b = add(mail, "a@example.com"), add(mail, "b@example.com")
    send(mail, NOW, [a, b])
    one, _ = mail.due_deliveries(NOW + 5_000)
    mail.claim(one.id, NOW + 6_000)  # one is in flight
    assert mail.cancel_pending() == 1  # the other is not
    assert due(mail, NOW + HOUR) == []
    assert mail.finish(one.id, "sent", NOW + 7_000) is True  # the one in flight finishes as ever


def test_a_copy_that_is_too_late_is_failed_with_the_reason_and_the_recipient_is_told(
    mail: MailStore,
) -> None:
    a = add(mail)
    send(mail, NOW, [a])
    assert mail.expire_late(NOW + 5 * HOUR, 6 * HOUR, "too late") == 0
    assert mail.expire_late(NOW + 7 * HOUR, 6 * HOUR, "too late to send") == 1
    assert due(mail, NOW + 7 * HOUR) == []
    row = mail.recipient(a)
    assert row is not None and row.last_status == "failed" and row.last_detail == "too late to send"
    assert row.last_kind == "daily"
    (sent,) = mail.sends()
    assert sent.failed == 1


def test_a_mail_is_done_when_all_its_copies_have_gone_one_way_or_the_other(mail: MailStore) -> None:
    a, b = add(mail, "a@example.com"), add(mail, "b@example.com")
    send(mail, NOW, [a, b])
    first, second = mail.due_deliveries(NOW + 5_000)
    mail.claim(first.id, NOW)
    mail.finish(first.id, "sent", NOW)
    assert mail.close_finished_sends() == 0 and mail.sends()[0].status == "sending"
    mail.claim(second.id, NOW)
    mail.finish(second.id, "failed", NOW, detail="refused")
    assert mail.close_finished_sends() == 1 and mail.sends()[0].status == "done"
    assert mail.close_finished_sends() == 0


def test_old_mails_and_their_copies_are_deleted_a_few_at_a_time(
    store: Store, mail: MailStore
) -> None:
    a = add(mail)
    for day in range(5):
        send(mail, NOW + day * 24 * HOUR, [a])
    assert mail.delete_sends_before(NOW + 3 * 24 * HOUR, limit=2) == 2
    assert mail.delete_sends_before(NOW + 3 * 24 * HOUR, limit=2) == 1
    assert mail.delete_sends_before(NOW + 3 * 24 * HOUR, limit=2) == 0
    assert [s.scheduled_at for s in mail.sends()] == [NOW + 4 * 24 * HOUR, NOW + 3 * 24 * HOUR]
    with store.transaction() as s:
        assert s.execute(text("SELECT count(*) FROM mail_deliveries")).scalar_one() == 2


def test_the_default_days_are_all_seven() -> None:
    assert MailSchedule().days == ALL_DAYS
