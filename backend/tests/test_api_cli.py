import asyncio
import contextlib
import io
import socket
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi.testclient import TestClient

from trafficpi.api import admin, cli
from trafficpi.api.app import create_app
from trafficpi.api.auth import verify_password
from trafficpi.api.settings import ApiSettings
from trafficpi.db.store import open_store

PASSWORD = "a long enough password"


def set_password(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch, password: str, *extra: str
) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(password + "\n"))
    argv = ["--data-dir", str(data_dir), "set-password", "--password-stdin", *extra]
    return admin.main(argv)


def test_set_password_creates_the_user_then_changes_the_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert set_password(tmp_path, monkeypatch, PASSWORD) == 0
    assert "created user 'admin'" in capsys.readouterr().out
    user = open_store(tmp_path).get_user("admin")
    assert user is not None and verify_password(user.password_hash, PASSWORD)

    assert set_password(tmp_path, monkeypatch, "something else entirely") == 0
    assert "changed the password of 'admin'" in capsys.readouterr().out
    store = open_store(tmp_path)
    assert store.user_count() == 1
    user = store.get_user("admin")
    assert user is not None and verify_password(user.password_hash, "something else entirely")


def test_the_user_made_by_the_command_can_log_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert set_password(tmp_path, monkeypatch, PASSWORD, "--username", "Owner") == 0
    app = create_app(ApiSettings(data_dir=tmp_path, dashboard_dir=None))
    with TestClient(app) as client:
        login = {"username": "owner", "password": PASSWORD}
        assert client.post("/api/auth/login", json=login).status_code == 204
        assert client.get("/api/auth/me").json()["username"] == "owner"


def test_a_short_password_or_an_empty_username_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert set_password(tmp_path, monkeypatch, "short") == 1
    assert "at least" in capsys.readouterr().err
    assert set_password(tmp_path, monkeypatch, PASSWORD, "--username", "  ") == 1
    assert "username is empty" in capsys.readouterr().err
    assert open_store(tmp_path).user_count() == 0


def test_without_a_terminal_the_password_must_come_from_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert admin.main(["--data-dir", str(tmp_path), "set-password"]) == 1
    assert "--password-stdin" in capsys.readouterr().err


def test_the_interactive_prompt_asks_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Terminal(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr("sys.stdin", Terminal())
    answers = iter([PASSWORD, "typo", PASSWORD, PASSWORD])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    argv = ["--data-dir", str(tmp_path), "set-password"]
    assert admin.main(argv) == 1  # the second entry differs
    assert "differ" in capsys.readouterr().err
    assert admin.main(argv) == 0
    assert open_store(tmp_path).user_count() == 1


def test_settings_come_from_flags_over_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRAFFICPI_DATA_DIR", str(tmp_path / "from-env"))
    monkeypatch.setenv("TRAFFICPI_VISION_URL", "http://env:1")
    parser = cli.build_parser()
    from_env = cli.settings_from(parser.parse_args([]))
    assert from_env.data_dir == tmp_path / "from-env" and from_env.vision_url == "http://env:1"
    assert not from_env.trust_proxy_headers
    flags = [
        "--data-dir",
        str(tmp_path / "x"),
        "--vision-url",
        "http://flag:2",
        "--trust-proxy-headers",
    ]
    from_flags = cli.settings_from(parser.parse_args(flags))
    assert from_flags.data_dir == tmp_path / "x" and from_flags.vision_url == "http://flag:2"
    assert from_flags.trust_proxy_headers


def test_development_origins_come_from_flags_or_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parser = cli.build_parser()
    assert cli.settings_from(parser.parse_args([])).allowed_origins == ()
    flags = ["--allow-origin", "http://127.0.0.1:3100/", "--allow-origin", "HTTP://Dev.Local:3000"]
    assert cli.settings_from(parser.parse_args(flags)).allowed_origins == (
        "http://127.0.0.1:3100",
        "http://dev.local:3000",
    )
    monkeypatch.setenv("TRAFFICPI_ALLOWED_ORIGINS", "http://a:1, http://b:2/ ,")
    assert cli.settings_from(parser.parse_args([])).allowed_origins == ("http://a:1", "http://b:2")


def test_main_serves_the_app_without_trusting_proxy_headers_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    served: dict[str, Any] = {}

    def fake_run(server: cli.Server) -> None:
        served.update(config=server.config, app=server._app)

    monkeypatch.setattr(cli.Server, "run", fake_run)
    caplog.set_level("INFO")
    assert cli.main(["--data-dir", str(tmp_path), "--port", "8123", "--host", "127.0.0.1"]) == 0
    config = served["config"]
    assert config.host == "127.0.0.1" and config.port == 8123
    assert config.proxy_headers is False and config.server_header is False
    assert "no user exists yet" in caplog.text  # tells the owner what to do next
    assert served["app"].state.ctx.settings.data_dir == tmp_path


def test_the_service_looks_at_the_daily_email_every_half_minute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    given: dict[str, Any] = {}
    real = cli.create_app

    def spy(settings: ApiSettings, **kwargs: Any) -> Any:
        given.update(kwargs)
        return real(settings, **kwargs)

    monkeypatch.setattr(cli, "create_app", spy)
    monkeypatch.setattr(cli.Server, "run", lambda server: None)
    assert cli.main(["--data-dir", str(tmp_path), "--host", "127.0.0.1"]) == 0
    assert given["mail_tick_s"] == 30.0


def test_the_daily_email_goes_out_through_resend_unless_a_flag_or_the_environment_says_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TRAFFICPI_MAIL_TRANSPORT", raising=False)
    parser = cli.build_parser()
    assert cli.settings_from(parser.parse_args([])).mail_transport == "resend"
    assert (
        cli.settings_from(parser.parse_args(["--mail-transport", "file"])).mail_transport == "file"
    )
    monkeypatch.setenv("TRAFFICPI_MAIL_TRANSPORT", "file")
    assert cli.settings_from(parser.parse_args([])).mail_transport == "file"
    flags = ["--mail-transport", "resend"]
    assert cli.settings_from(parser.parse_args(flags)).mail_transport == "resend"  # the flag wins
    with pytest.raises(SystemExit):
        parser.parse_args(["--mail-transport", "carrier-pigeon"])


def test_a_shutdown_signal_tells_the_app_to_end_its_streams(tmp_path: Path) -> None:
    import signal

    app = create_app(ApiSettings(data_dir=tmp_path, dashboard_dir=None))
    server = cli.Server(uvicorn.Config(app), app)
    assert not app.state.ctx.stopping.is_set() and not server.should_exit
    server.handle_exit(signal.SIGTERM, None)
    assert app.state.ctx.stopping.is_set() and server.should_exit


# --- who is believed about the client's address (plan.md D31) -------------------------------------


def settings_for(*argv: str) -> ApiSettings:
    return cli.settings_from(cli.build_parser().parse_args(list(argv)))


def test_proxy_headers_are_off_and_loopback_is_the_only_proxy_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TRAFFICPI_TRUST_PROXY_HEADERS", raising=False)
    monkeypatch.delenv("TRAFFICPI_TRUSTED_PROXIES", raising=False)
    settings = settings_for()
    assert settings.trust_proxy_headers is False
    assert settings.trusted_proxies == ("127.0.0.1", "::1")


def test_the_flags_switch_proxy_headers_on_and_add_proxies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TRAFFICPI_TRUSTED_PROXIES", raising=False)
    settings = settings_for(
        "--trust-proxy-headers", "--trusted-proxy", "10.0.0.5", "--trusted-proxy", "10.0.0.6"
    )
    assert settings.trust_proxy_headers is True
    assert settings.trusted_proxies == ("127.0.0.1", "::1", "10.0.0.5", "10.0.0.6")


def test_the_environment_can_do_the_same_so_that_a_unit_needs_no_new_command_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRAFFICPI_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("TRAFFICPI_TRUSTED_PROXIES", "10.1.1.1, 10.2.2.2")
    settings = settings_for()
    assert settings.trust_proxy_headers is True
    assert settings.trusted_proxies == ("127.0.0.1", "::1", "10.1.1.1", "10.2.2.2")
    monkeypatch.setenv("TRAFFICPI_TRUST_PROXY_HEADERS", "yes")  # only "1" switches it on
    assert settings_for().trust_proxy_headers is False


# --- telling systemd it is up, and that it still answers (plan.md D27) ---------------------------


def serve_in_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **env: str
) -> tuple[threading.Thread, cli.Server, socket.socket]:
    """The real API server under uvicorn, with a fake systemd to listen to it."""
    systemd = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    systemd.bind(str(tmp_path / "notify"))
    systemd.settimeout(0.2)
    monkeypatch.setenv("NOTIFY_SOCKET", str(tmp_path / "notify"))
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    app = create_app(ApiSettings(data_dir=tmp_path / "data", dashboard_dir=None))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = cli.Server(config, app)
    thread = threading.Thread(target=server.run)
    thread.start()
    return thread, server, systemd


def messages_until(systemd: socket.socket, done, seconds: float = 20) -> list[str]:  # type: ignore[no-untyped-def]
    got: list[str] = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not done(got):
        with contextlib.suppress(TimeoutError):
            got.append(systemd.recv(4096).decode())
    return got


def test_the_api_says_ready_once_it_listens_and_then_keeps_the_watchdog_fed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    thread, server, systemd = serve_in_thread(tmp_path, monkeypatch, WATCHDOG_USEC="600000")
    try:
        got = messages_until(systemd, lambda m: m.count("WATCHDOG=1") >= 3)
        assert got[0].startswith("READY=1")  # the first message, and nothing before it
        assert got.count("WATCHDOG=1") >= 3
    finally:
        server.should_exit = True
        thread.join(20)
        systemd.close()


def test_the_api_without_a_watchdog_limit_says_ready_and_nothing_more(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WATCHDOG_USEC", raising=False)
    thread, server, systemd = serve_in_thread(tmp_path, monkeypatch)
    try:
        got = messages_until(systemd, lambda m: len(m) >= 1)
        time.sleep(0.6)
        got += messages_until(systemd, lambda m: True, seconds=0.5)
        assert got and got[0].startswith("READY=1") and "WATCHDOG=1" not in got
    finally:
        server.should_exit = True
        thread.join(20)
        systemd.close()


def test_an_exhausted_thread_pool_stops_the_pings_though_the_event_loop_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every blocking endpoint needs a worker thread: if none can be had, the dashboard is dead
    even though the event loop is fine, and the watchdog should hear nothing."""

    async def never(function, *args, **kwargs):  # type: ignore[no-untyped-def]
        await asyncio.Event().wait()

    monkeypatch.setattr(cli, "run_in_threadpool", never)
    thread, server, systemd = serve_in_thread(tmp_path, monkeypatch, WATCHDOG_USEC="600000")
    try:
        got = messages_until(systemd, lambda m: False, seconds=2.5)
        assert got and got[0].startswith("READY=1")
        assert "WATCHDOG=1" not in got
    finally:
        server.should_exit = True
        thread.join(20)
        systemd.close()


# --- backup and restore from the command line (plan.md D29) ---------------------------------------


def a_data_dir_with_something_in_it(path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    assert set_password(path, monkeypatch, PASSWORD) == 0
    return path


def test_backup_writes_a_file_and_says_where_and_how_big(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    data = a_data_dir_with_something_in_it(tmp_path / "data", monkeypatch)
    capsys.readouterr()
    out = tmp_path / "out"
    assert admin.main(["--data-dir", str(data), "backup", "--to", str(out)]) == 0
    said = capsys.readouterr().out
    written = list(out.glob("trafficpi-backup-*.tar.gz"))
    assert len(written) == 1 and str(written[0]) in said
    assert "password" in said  # it says that the file is sensitive


def test_backup_goes_to_the_backups_folder_in_the_data_directory_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = a_data_dir_with_something_in_it(tmp_path / "data", monkeypatch)
    assert admin.main(["--data-dir", str(data), "backup"]) == 0
    assert len(list((data / "backups").glob("trafficpi-backup-*.tar.gz"))) == 1


def test_backup_options_are_passed_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json
    import tarfile

    data = a_data_dir_with_something_in_it(tmp_path / "data", monkeypatch)
    (data / "evidence").mkdir()
    (data / "evidence" / "a.jpg").write_bytes(b"x")
    out = tmp_path / "out"
    argv = ["--data-dir", str(data), "backup", "--to", str(out)]
    assert admin.main([*argv, "--config-only", "--with-evidence"]) == 0
    with tarfile.open(next(out.glob("*.tar.gz"))) as tar:
        manifest = json.load(tar.extractfile("manifest.json"))  # type: ignore[arg-type]
        assert manifest["config_only"] and manifest["with_evidence"]
        assert "evidence/a.jpg" in tar.getnames()


def test_backup_keeps_only_the_newest_few(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = a_data_dir_with_something_in_it(tmp_path / "data", monkeypatch)
    out = tmp_path / "out"
    for _ in range(4):
        assert admin.main(["--data-dir", str(data), "backup", "--to", str(out), "--keep", "2"]) == 0
    assert len(list(out.glob("trafficpi-backup-*.tar.gz"))) == 2


def test_restore_puts_a_backup_back_and_says_what_to_do_next(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    data = a_data_dir_with_something_in_it(tmp_path / "data", monkeypatch)
    out = tmp_path / "out"
    assert admin.main(["--data-dir", str(data), "backup", "--to", str(out)]) == 0
    archive = next(out.glob("*.tar.gz"))
    capsys.readouterr()
    fresh = tmp_path / "fresh"
    assert admin.main(["--data-dir", str(fresh), "restore", str(archive)]) == 0
    said = capsys.readouterr().out
    assert "restored" in said and "systemctl start" in said
    user = open_store(fresh).get_user("admin")
    assert user is not None and verify_password(user.password_hash, PASSWORD)


def test_restore_refuses_with_a_reason_while_the_database_is_in_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trafficpi.api import backup

    data = a_data_dir_with_something_in_it(tmp_path / "data", monkeypatch)
    assert admin.main(["--data-dir", str(data), "backup", "--to", str(tmp_path / "out")]) == 0
    archive = next((tmp_path / "out").glob("*.tar.gz"))
    capsys.readouterr()
    monkeypatch.setattr(backup, "holders_of", lambda path: [(77, "trafficpi-vision run")])
    assert admin.main(["--data-dir", str(data), "restore", str(archive)]) == 1
    assert "in use" in capsys.readouterr().err


def test_users_lists_who_can_log_in_and_nothing_before_anyone_can(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The installer asks this to tell a first install (set a password) from a second run."""
    assert admin.main(["--data-dir", str(tmp_path), "users"]) == 0
    assert capsys.readouterr().out == ""
    assert set_password(tmp_path, monkeypatch, PASSWORD, "--username", "Zed") == 0
    assert set_password(tmp_path, monkeypatch, PASSWORD, "--username", "amy") == 0
    capsys.readouterr()
    assert admin.main(["--data-dir", str(tmp_path), "users"]) == 0
    assert capsys.readouterr().out == "amy\nzed\n"  # sorted, lower case, no hashes
