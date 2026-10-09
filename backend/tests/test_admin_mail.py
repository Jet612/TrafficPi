"""trafficpi-admin's commands for the daily email: the key, and what the mail would say (D32)."""

import io
import stat
from pathlib import Path

import pytest

from trafficpi.api import admin
from trafficpi.db.store import open_store
from trafficpi.mail.settings import read_key

KEY = "re_SECRETsecret123456789"


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def run(data_dir: Path, *args: str) -> int:
    return admin.main(["--data-dir", str(data_dir), *args])


# --- the key ----------------------------------------------------------------------------------


def test_the_key_can_be_piped_in_and_is_kept_privately_without_being_printed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(KEY + "\n"))
    assert run(tmp_path, "set-mail-key", "--key-stdin") == 0
    shown = capsys.readouterr()
    assert KEY not in shown.out + shown.err and "SECRETsecret" not in shown.out + shown.err
    assert read_key(tmp_path / "mail.key") == KEY
    assert stat.S_IMODE((tmp_path / "mail.key").stat().st_mode) == 0o600
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700


def test_the_key_is_asked_for_without_showing_what_is_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", Terminal())
    prompts: list[str] = []

    def getpass(prompt: str = "") -> str:
        prompts.append(prompt)
        return KEY

    monkeypatch.setattr("getpass.getpass", getpass)
    assert run(tmp_path, "set-mail-key") == 0
    assert len(prompts) == 1 and "key" in prompts[0].lower()
    assert read_key(tmp_path / "mail.key") == KEY
    out = capsys.readouterr()
    assert KEY not in out.out + out.err


def test_without_a_terminal_the_key_must_come_from_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert run(tmp_path, "set-mail-key") == 1
    assert "--key-stdin" in capsys.readouterr().err
    assert not (tmp_path / "mail.key").exists()


@pytest.mark.parametrize("typed", ["", "short", "has a space in it", "x" * 400])
def test_a_key_that_cannot_be_one_is_refused_without_repeating_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    typed: str,
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(typed + "\n"))
    assert run(tmp_path, "set-mail-key", "--key-stdin") == 1
    err = capsys.readouterr().err
    assert "key" in err
    if typed.strip():
        assert typed.strip() not in err
    assert not (tmp_path / "mail.key").exists()


def test_a_key_that_is_there_can_be_removed_and_removing_again_is_fine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(KEY + "\n"))
    run(tmp_path, "set-mail-key", "--key-stdin")
    assert run(tmp_path, "set-mail-key", "--remove") == 0
    assert not (tmp_path / "mail.key").exists()
    assert run(tmp_path, "set-mail-key", "--remove") == 0
    assert "removed" in capsys.readouterr().out


# --- the digest -------------------------------------------------------------------------------


def test_the_digest_prints_what_the_mail_would_say_and_sends_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    open_store(tmp_path).close()
    assert run(tmp_path, "digest") == 0
    out = capsys.readouterr().out
    assert out.startswith("Subject: [Test] ")
    assert "TrafficPi daily report" in out and "No stop sign is set up" in out  # an empty database
    assert not (tmp_path / "outbox").exists()
    assert not (tmp_path / "mail.key").exists()


def test_the_digest_can_write_the_html_to_a_file_to_open_in_a_browser(tmp_path: Path) -> None:
    open_store(tmp_path).close()
    target = tmp_path / "look.html"
    assert run(tmp_path, "digest", "--html", str(target)) == 0
    html = target.read_text()
    assert html.lstrip().lower().startswith("<!doctype html>") and "TrafficPi daily report" in html
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_the_digest_says_which_time_zone_it_used(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = open_store(tmp_path)
    store.set_setting("timezone", "Asia/Kolkata")
    store.close()
    run(tmp_path, "digest")
    assert "IST" in capsys.readouterr().out
