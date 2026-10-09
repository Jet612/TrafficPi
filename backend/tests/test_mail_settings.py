import os
import stat
from pathlib import Path

import pytest

from trafficpi.mail.settings import (
    ALL_DAYS,
    DEFAULT_TIME,
    MailSchedule,
    clean_address,
    clean_key,
    clean_link,
    clean_name,
    clear_key,
    format_sender,
    parse_time,
    read_key,
    write_key,
)

# --- the schedule ---------------------------------------------------------------------------------


def test_the_email_is_off_until_turned_on_and_goes_at_eight_in_the_evening_every_day() -> None:
    schedule = MailSchedule()
    assert schedule.enabled is False
    assert schedule.time == DEFAULT_TIME == "20:00"
    assert schedule.days == ALL_DAYS == (0, 1, 2, 3, 4, 5, 6)
    assert schedule.sender_name == "TrafficPi" and schedule.sender_address == ""
    assert schedule.link_url == ""


@pytest.mark.parametrize("text", ["00:00", "07:05", "20:00", "23:59"])
def test_a_time_of_day_is_hours_and_minutes(text: str) -> None:
    hour, minute = parse_time(text)
    assert f"{hour:02d}:{minute:02d}" == text
    assert MailSchedule(time=text).time == text


@pytest.mark.parametrize(
    "text", ["", "8:00", "24:00", "20:60", "20:0", "2000", "20:00:00", "20.00", " 20:00", "ab:cd"]
)
def test_a_time_that_is_not_hh_mm_is_refused(text: str) -> None:
    with pytest.raises(ValueError, match="HH:MM"):
        parse_time(text)
    with pytest.raises(ValueError, match="HH:MM"):
        MailSchedule(time=text)


@pytest.mark.parametrize("days", [(), (7,), (-1,), (0, 0), (0, 1, 1), (3, 2)])
def test_the_days_are_some_of_monday_to_sunday_once_each_in_order(days: tuple[int, ...]) -> None:
    with pytest.raises(ValueError, match="day"):
        MailSchedule(days=days)


def test_any_run_of_days_in_order_is_fine() -> None:
    assert MailSchedule(days=(0,)).days == (0,)
    assert MailSchedule(days=(0, 1, 2, 3, 4)).days == (0, 1, 2, 3, 4)
    assert MailSchedule(days=(5, 6)).days == (5, 6)


# --- addresses ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("jet@example.com", "jet@example.com"),
        ("  Jet@Example.COM ", "jet@example.com"),
        ("first.last+traffic@mail.example.co.uk", "first.last+traffic@mail.example.co.uk"),
        ("o'brien@example.org", "o'brien@example.org"),
        ("a@b.io", "a@b.io"),
    ],
)
def test_an_address_is_checked_and_kept_in_lower_case(typed: str, stored: str) -> None:
    assert clean_address(typed) == stored


@pytest.mark.parametrize(
    "typed",
    [
        "",
        "   ",
        "plain",
        "@example.com",
        "jet@",
        "jet@example",  # no dot: not a place that takes mail
        "jet@@example.com",
        "jet@exa mple.com",
        "jet @example.com",
        "Jet <jet@example.com>",  # a name and brackets are not an address
        "jet@example.com, other@example.com",  # two are not one
        "jet@example.com\r\nBcc: other@example.com",  # a header smuggled in
        ".jet@example.com",
        "jet.@example.com",
        "je..t@example.com",
        "jet@-example.com",
        "jet@example-.com",
        "jet@example..com",
        "jet@example.123",
        "jet@exämple.com",  # only the plain ASCII form
        "‮jet@example.com",
        f"{'a' * 65}@example.com",
        f"jet@{'a' * 64}.com",
        f"{'a' * 60}@{'b' * 63}.{'c' * 63}.{'d' * 63}.{'e' * 63}.com",
    ],
)
def test_an_address_that_is_not_one_is_refused_in_words(typed: str) -> None:
    with pytest.raises(ValueError, match="address"):
        clean_address(typed)


def test_whitespace_around_an_address_is_trimmed_and_leaves_nothing_in_it() -> None:
    assert clean_address("\tjet@example.com \r\n") == "jet@example.com"
    assert "\n" not in clean_address("jet@example.com\n")


# --- the sender -----------------------------------------------------------------------------------


def test_the_sender_is_a_name_and_an_address() -> None:
    assert format_sender("TrafficPi", "report@mail.example.com") == (
        "TrafficPi <report@mail.example.com>"
    )
    assert format_sender("", "report@mail.example.com") == "report@mail.example.com"


@pytest.mark.parametrize(
    "name", ["Elm St, cam", "Mr. Smith", "Street: north", "a;b", "cam (north)", "a@b", "[cam]"]
)
def test_a_sender_name_with_a_character_the_standard_reserves_is_put_in_quotes(name: str) -> None:
    assert clean_name(name) == name  # it is allowed ...
    assert (
        format_sender(name, "r@mail.example.com") == f'"{name}" <r@mail.example.com>'
    )  # ... quoted


def test_a_plain_sender_name_is_not_quoted() -> None:
    assert format_sender("Elm Street cam-2", "r@mail.example.com") == (
        "Elm Street cam-2 <r@mail.example.com>"
    )


@pytest.mark.parametrize("name", ["TrafficPi", "Elm Street cam", "Café-1", "A" * 60, ""])
def test_a_sender_name_is_plain_text_up_to_sixty_characters(name: str) -> None:
    assert clean_name(f"  {name} ") == name


@pytest.mark.parametrize(
    "name", ["A" * 61, "Jet <jet@x.com>", 'say "hi"', "a\nb", "a\x00b", "a\\b"]
)
def test_a_sender_name_with_brackets_quotes_or_control_characters_is_refused(name: str) -> None:
    with pytest.raises(ValueError, match="name"):
        clean_name(name)


def test_the_sender_address_must_be_an_address_when_the_email_is_on() -> None:
    assert MailSchedule(enabled=False, sender_address="").sender_address == ""
    with pytest.raises(ValueError, match="address"):
        MailSchedule(enabled=True, sender_address="")
    with pytest.raises(ValueError, match="address"):
        MailSchedule(enabled=False, sender_address="not an address")
    assert MailSchedule(enabled=True, sender_address="a@b.io").enabled is True


# --- the link -------------------------------------------------------------------------------------


@pytest.mark.parametrize("link", ["", "http://192.168.1.20:8000", "https://traffic.example.com/"])
def test_a_link_is_empty_or_a_web_address(link: str) -> None:
    assert clean_link(f" {link} ") == link


@pytest.mark.parametrize(
    "link",
    [
        "javascript:alert(1)",
        "ftp://example.com",
        "//example.com",
        "example.com",
        "https://",
        "https://exa mple.com",
        'https://example.com/"onmouseover="x',
        "https://example.com/<b>",
        "https://example.com/a\nb",
        "https://" + "a" * 300 + ".com",
    ],
)
def test_a_link_that_is_not_a_plain_web_address_is_refused(link: str) -> None:
    with pytest.raises(ValueError, match="link"):
        clean_link(link)


# --- the key --------------------------------------------------------------------------------------


@pytest.mark.parametrize("typed", ["re_AbCdEf123456_xyz", "  re_AbCdEf123456_xyz\n", "abcdefgh"])
def test_a_key_is_one_token_without_spaces(typed: str) -> None:
    assert clean_key(typed) == typed.strip()


@pytest.mark.parametrize(
    "typed",
    ["", "   ", "short", "re_ has a space inside", "re_\nsecond line", "x" * 300, "re_ü123456"],
)
def test_a_key_that_cannot_be_one_is_refused_without_repeating_it(typed: str) -> None:
    with pytest.raises(ValueError, match="key") as raised:
        clean_key(typed)
    assert typed.strip() not in str(raised.value) or typed.strip() == ""


def test_the_key_is_kept_in_a_file_that_only_its_owner_can_read(tmp_path: Path) -> None:
    path = tmp_path / "mail.key"
    assert read_key(path) is None
    write_key(path, "re_AbCdEf123456_xyz")
    assert read_key(path) == "re_AbCdEf123456_xyz"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert list(tmp_path.iterdir()) == [path]  # no temporary file is left behind


def test_a_new_key_replaces_the_old_one_without_a_moment_of_none(tmp_path: Path) -> None:
    path = tmp_path / "mail.key"
    write_key(path, "re_first_key_123")
    write_key(path, "re_second_key_456")
    assert read_key(path) == "re_second_key_456"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_key_file_is_never_wider_than_600_even_if_the_old_one_was(tmp_path: Path) -> None:
    path = tmp_path / "mail.key"
    path.write_text("re_old_key_12345\n")
    path.chmod(0o644)
    write_key(path, "re_new_key_12345")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_the_key_can_be_removed_and_removing_it_again_is_fine(tmp_path: Path) -> None:
    path = tmp_path / "mail.key"
    write_key(path, "re_AbCdEf123456_xyz")
    clear_key(path)
    assert read_key(path) is None and not path.exists()
    clear_key(path)


def test_an_empty_or_damaged_key_file_counts_as_no_key(tmp_path: Path) -> None:
    path = tmp_path / "mail.key"
    path.write_text("\n")
    assert read_key(path) is None
    path.write_text("two words\n")
    assert read_key(path) is None
    path.write_bytes(b"\xff\xfe\x00")
    assert read_key(path) is None


def test_the_data_directory_is_made_private_when_the_key_is_the_first_file_in_it(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "fresh"
    write_key(folder / "mail.key", "re_AbCdEf123456_xyz")
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert os.access(folder / "mail.key", os.R_OK)
