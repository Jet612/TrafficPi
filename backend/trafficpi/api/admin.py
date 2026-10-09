"""Command-line entry point for administration: ``trafficpi-admin``."""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from trafficpi.api import backup
from trafficpi.api.auth import MIN_PASSWORD_LENGTH, hash_password, normalize_username
from trafficpi.api.settings import ApiSettings
from trafficpi.core.paths import DATA_DIR_HELP
from trafficpi.db.mail_store import MailStore
from trafficpi.db.store import open_store
from trafficpi.mail.mailer import MailMessage, SendOutcome
from trafficpi.mail.service import MailService
from trafficpi.mail.settings import clean_key, clear_key, write_key


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    if not sys.stdin.isatty():
        raise RuntimeError("no terminal to ask on; pipe the password and use --password-stdin")
    password = getpass.getpass("New password: ")
    if getpass.getpass("Repeat the password: ") != password:
        raise RuntimeError("the two passwords differ")
    return password


def cmd_set_password(args: argparse.Namespace) -> int:
    """Create a user, or change an existing user's password (which ends their sessions)."""
    username = normalize_username(args.username)
    if not username:
        raise RuntimeError("the username is empty")
    try:
        password_hash = hash_password(_read_password(args.password_stdin))
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    store = open_store(args.data_dir)
    if store.set_password_hash(username, password_hash):
        print(f"changed the password of {username!r}; they are logged out everywhere")
    else:
        store.create_user(username, password_hash)
        print(f"created user {username!r}")
    return 0


def _read_key(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    if not sys.stdin.isatty():
        raise RuntimeError("no terminal to ask on; pipe the key and use --key-stdin")
    return getpass.getpass("Resend API key (not shown): ")


def cmd_set_mail_key(args: argparse.Namespace) -> int:
    """Keep the email provider's key (asked for, so that it is not in the shell history), or take
    it away. It is read from its file at every mail: nothing needs restarting."""
    path = args.data_dir / "mail.key"
    if args.remove:
        clear_key(path)
        print("removed the email provider's key")
        return 0
    try:
        key = clean_key(_read_key(args.key_stdin))
    except ValueError as exc:
        raise RuntimeError(str(exc)) from None  # the message never repeats what was typed
    write_key(path, key)
    print(f"kept the key in {path} (readable by you only); the API uses it from the next mail")
    return 0


class _NeverSends:
    """What ``digest`` hands the mail service in place of a sender: it only builds the mail."""

    name = "none"

    def ready(self) -> bool:
        return True

    def send(self, message: MailMessage) -> SendOutcome:
        raise AssertionError("the digest command never sends")


def cmd_digest(args: argparse.Namespace) -> int:
    """Print what the daily email would say now (the past 24 hours); nothing is sent."""
    store = open_store(args.data_dir)
    try:
        digest = MailService(store, MailStore(store), _NeverSends()).preview()
    finally:
        store.close()
    print(f"Subject: {digest.subject}\n\n{digest.text}", end="")
    if args.html is not None:
        args.html.write_text(digest.html, encoding="utf-8")
        args.html.chmod(0o600)
        print(f"\n(the HTML version is in {args.html}; open it in a browser)")
    return 0


def cmd_users(args: argparse.Namespace) -> int:
    """List who can log in, one name to a line (nothing at all when there is no user yet)."""
    for name in open_store(args.data_dir).usernames():
        print(name)
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    """Write a backup of the database and the files beside it."""
    archive = backup.create_backup(
        args.data_dir,
        args.to,
        config_only=args.config_only,
        with_evidence=args.with_evidence,
        keep=args.keep,
    )
    size_mb = archive.stat().st_size / 1e6
    print(f"wrote {archive} ({size_mb:.1f} MB)")
    print(
        "It holds the password hash and the key that signs the sessions: keep it private, and "
        "copy it off this Pi (a backup on the same card does not survive the card)."
    )
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    """Put a backup back. The services must be stopped; what it replaces is set aside."""
    report = backup.restore_backup(args.archive, args.data_dir)
    rows = report.rows
    print(
        f"restored the backup made {report.created} (database schema {report.schema}): "
        f"{rows.get('events', 0)} events, {rows.get('trails', 0)} trails, "
        f"{rows.get('users', 0)} user(s)" + (", configuration only" if report.config_only else "")
    )
    if report.set_aside is not None:
        print(f"what it replaced is in {report.set_aside}; delete that folder when you are happy")
    print("start the services again: sudo systemctl start trafficpi-vision trafficpi-api")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trafficpi-admin", description="Administer a TrafficPi installation."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=ApiSettings().data_dir,
        help=f"where the database lives ({DATA_DIR_HELP})",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    pw = sub.add_parser(
        "set-password",
        help="create the admin user or change a password",
        description=f"Passwords need at least {MIN_PASSWORD_LENGTH} characters.",
    )
    pw.add_argument("--username", default="admin")
    pw.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the password from the first line of standard input instead of asking",
    )
    pw.set_defaults(func=cmd_set_password)

    us = sub.add_parser("users", help="list who can log in (one name to a line)")
    us.set_defaults(func=cmd_users)

    bk = sub.add_parser(
        "backup",
        help="write a backup of the database and its files (the services may keep running)",
        description="One .tar.gz readable by you only. It is a consistent snapshot even while "
        "the services write to the database.",
    )
    bk.add_argument(
        "--to",
        type=Path,
        help="the folder for it (default: backups, inside the data directory)",
    )
    bk.add_argument(
        "--keep",
        type=int,
        metavar="N",
        help="afterwards keep only the N newest backups in that folder",
    )
    bk.add_argument(
        "--config-only",
        action="store_true",
        help="leave out the history (events, counts, trails and the reviews of stop events): "
        "the settings, the profile, the users and the key only",
    )
    bk.add_argument(
        "--with-evidence",
        action="store_true",
        help="also include the pictures and clips of stop-sign passes (they can be large)",
    )
    bk.set_defaults(func=cmd_backup)

    rs = sub.add_parser(
        "restore",
        help="put a backup back (stop the services first)",
        description="Checks the archive and the database in it before changing anything, sets "
        "the files it replaces aside instead of deleting them, and refuses while a process has "
        "the database open.",
    )
    rs.add_argument("archive", type=Path, help="a backup made by `trafficpi-admin backup`")
    rs.set_defaults(func=cmd_restore)

    mk = sub.add_parser(
        "set-mail-key",
        help="keep the daily email's provider key (Resend), asked for without echo",
        description="The key goes into mail.key beside the database, readable by you only, and "
        "is never shown, logged or sent anywhere but the provider.",
    )
    mk.add_argument(
        "--key-stdin",
        action="store_true",
        help="read the key from the first line of standard input instead of asking",
    )
    mk.add_argument("--remove", action="store_true", help="remove the stored key instead")
    mk.set_defaults(func=cmd_set_mail_key)

    dg = sub.add_parser(
        "digest",
        help="print what the daily email would say now (it never sends anything)",
        description="Builds the mail for the past 24 hours from the database and prints it. "
        "Nothing is sent and nothing is kept.",
    )
    dg.add_argument("--html", type=Path, help="also write the HTML version to this file")
    dg.set_defaults(func=cmd_digest)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
