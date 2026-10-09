"""Command-line entry point for administration: ``trafficpi-admin``."""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from trafficpi.api.auth import MIN_PASSWORD_LENGTH, hash_password, normalize_username
from trafficpi.api.settings import ApiSettings
from trafficpi.core.paths import DATA_DIR_HELP
from trafficpi.db.store import open_store


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
