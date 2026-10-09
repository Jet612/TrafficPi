import io
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
        assert client.get("/api/auth/me").json() == {"username": "owner"}


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
