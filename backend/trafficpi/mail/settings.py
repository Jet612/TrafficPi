"""The daily email's settings, and the checks that keep a bad value from reaching a mail header.

The schedule lives in the database's settings table, like the other settings, so that the dashboard
can change it and the API's scheduler reads the change at its next look (no restart). The provider's
key does not: it is kept in a file of its own, readable by its owner only, beside the session key,
so that it is never in a settings dump, an API answer or a log line.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from trafficpi.core.paths import ensure_private_dir

ENABLED_KEY = "mail_enabled"
TIME_KEY = "mail_time"
DAYS_KEY = "mail_days"
SENDER_NAME_KEY = "mail_sender_name"
SENDER_ADDRESS_KEY = "mail_sender_address"
LINK_KEY = "mail_link_url"
ENABLED_SINCE_KEY = "mail_enabled_since"  # ms: when the owner last switched the email on

DEFAULT_TIME = "20:00"
ALL_DAYS = (0, 1, 2, 3, 4, 5, 6)  # Monday is 0, as in Python's ``weekday()``
DEFAULT_SENDER_NAME = "TrafficPi"
MAX_RECIPIENTS = 20
MAX_NAME = 60
MAX_LINK = 300
MAX_ADDRESS = 254

_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_LOCAL = re.compile(r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+$")
_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_KEY = re.compile(r"^[A-Za-z0-9_.~+/=-]{8,256}$")  # what an API key looks like; no spaces
_NOT_IN_A_NAME = set('<>"\\')
_SPECIALS = set("()[]:;@,.")  # what a display name may not hold unquoted (RFC 5322)


def parse_time(text: str) -> tuple[int, int]:
    """Hour and minute of a ``HH:MM`` time of day (24-hour clock); anything else is refused."""
    found = _TIME.match(text) if isinstance(text, str) else None
    if found is None:
        raise ValueError("The time of day must be written HH:MM, like 20:00.")
    return int(found.group(1)), int(found.group(2))


def clean_address(text: str) -> str:
    """An email address in the plain ASCII form, in lower case; ValueError says what is wrong.

    Stricter than the standard on purpose: no names, no brackets, no quotes, no comments, no
    non-ASCII. What is accepted can be put into a mail header as it is.
    """
    address = text.strip() if isinstance(text, str) else ""
    if not address:
        raise ValueError("Enter an email address.")
    if len(address) > MAX_ADDRESS:
        raise ValueError("That email address is too long.")
    local, at, domain = address.rpartition("@")
    labels = domain.split(".")
    ok = (
        bool(at)
        and 0 < len(local) <= 64
        and _LOCAL.match(local) is not None
        and not local.startswith(".")
        and not local.endswith(".")
        and ".." not in local
        and len(labels) >= 2
        and all(_LABEL.match(label) for label in labels)
        and not labels[-1].isdigit()
    )
    if not ok:
        raise ValueError("That does not look like an email address; use the form name@example.com.")
    return address.lower()


def clean_name(text: str) -> str:
    """The name that shows as the sender: plain text, no brackets or quotes, up to 60 characters."""
    name = text.strip() if isinstance(text, str) else ""
    if len(name) > MAX_NAME:
        raise ValueError(f"The sender name can have at most {MAX_NAME} characters.")
    if any(c in _NOT_IN_A_NAME or not c.isprintable() for c in name):
        raise ValueError('The sender name cannot hold < > " \\ or control characters.')
    return name


def clean_link(text: str) -> str:
    """Empty, or a plain http(s) address to open the dashboard at; anything else is refused."""
    link = text.strip() if isinstance(text, str) else ""
    if not link:
        return ""
    try:
        parts = urlsplit(link)
        usable = (
            len(link) <= MAX_LINK
            and parts.scheme in ("http", "https")
            and bool(parts.hostname)
            and all(c.isprintable() and not c.isspace() and c not in '<>"\\' for c in link)
        )
    except ValueError:
        usable = False
    if not usable:
        raise ValueError("The link must be a web address starting with http:// or https://.")
    return link


def format_sender(name: str, address: str) -> str:
    """The ``From`` value: ``Name <address>``, or the address alone when there is no name.

    A name with one of the characters that the mail standard calls specials (a comma, a dot, a
    colon, a bracket, an at sign) is put in quotes, as the standard asks: unquoted, "Elm St, cam"
    reads as two addresses to a parser that follows it. (A quote or a backslash cannot be in a name:
    ``clean_name`` refuses them, so quoting never needs escapes.)
    """
    if not name:
        return address
    if any(c in _SPECIALS for c in name):
        return f'"{name}" <{address}>'
    return f"{name} <{address}>"


@dataclass(frozen=True, slots=True)
class MailSchedule:
    """When the daily email goes and who it is from; checked when it is made."""

    enabled: bool = False  # off until the owner turns it on
    time: str = DEFAULT_TIME  # HH:MM in the dashboard's time zone
    days: tuple[int, ...] = ALL_DAYS  # which days of the week, Monday 0 .. Sunday 6
    sender_name: str = DEFAULT_SENDER_NAME
    sender_address: str = ""
    link_url: str = ""  # where "open the dashboard" goes; none if empty

    def __post_init__(self) -> None:
        parse_time(self.time)
        in_range = all(
            isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6 for d in self.days
        )
        if not (self.days and in_range and list(self.days) == sorted(set(self.days))):
            raise ValueError("Pick at least one day of the week.")
        clean_name(self.sender_name)
        if self.sender_address or self.enabled:
            clean_address(self.sender_address)
        clean_link(self.link_url)

    @property
    def sender(self) -> str:
        return format_sender(self.sender_name, self.sender_address)


# --- the key --------------------------------------------------------------------------------------


def clean_key(text: str) -> str:
    """The provider's key as typed, without the spaces around it; refused if it cannot be a key.

    The refusal never repeats what was typed: it may be a key with a stray character in it.
    """
    key = text.strip() if isinstance(text, str) else ""
    if not _KEY.match(key):
        raise ValueError(
            "That does not look like an API key: it should be one string of letters, digits and "
            "underscores, with no spaces."
        )
    return key


def read_key(path: Path) -> str | None:
    """The key in the file, or None if there is no file or nothing usable in it."""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return text if _KEY.match(text) else None


def write_key(path: Path, key: str) -> None:
    """Keep the key in ``path``, readable by its owner only. The old key is replaced in one step,
    so a reader sees the old one or the new one and never a half-written file."""
    ensure_private_dir(path.parent)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".mail-key-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(key + "\n")
        Path(temporary).chmod(0o600)
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def clear_key(path: Path) -> None:
    path.unlink(missing_ok=True)
