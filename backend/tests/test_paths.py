from pathlib import Path

import pytest

from trafficpi.api.admin import build_parser as admin_parser
from trafficpi.api.settings import ApiSettings
from trafficpi.core.paths import DATA_DIR_ENV, default_data_dir
from trafficpi.vision import cli as vision_cli

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def no_data_dir_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)


def test_the_environment_variable_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path / "elsewhere"))
    assert default_data_dir() == tmp_path / "elsewhere"


def test_an_empty_variable_is_the_same_as_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DATA_DIR_ENV, "")
    assert default_data_dir() == BACKEND / "data"


def test_in_a_checkout_it_is_backend_data_wherever_the_command_is_run_from(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert default_data_dir() == BACKEND / "data"
    monkeypatch.chdir(tmp_path)  # a command run from anywhere else gets the same place
    assert default_data_dir() == BACKEND / "data"
    assert default_data_dir().is_absolute()


def test_without_a_checkout_it_falls_back_to_data_in_the_current_directory(tmp_path: Path) -> None:
    installed = tmp_path / "site-packages" / "trafficpi" / "core" / "paths.py"
    assert default_data_dir(installed) == Path("data")  # no pyproject.toml two levels up
    (tmp_path / "site-packages").mkdir()
    (tmp_path / "site-packages" / "pyproject.toml").write_text("")
    assert default_data_dir(installed) == tmp_path / "site-packages" / "data"


def test_the_commands_and_the_services_settings_agree_on_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The admin command, the vision service and the API must open the same database."""
    monkeypatch.chdir(tmp_path)
    expected = BACKEND / "data"
    assert ApiSettings().data_dir == expected
    assert vision_cli._data_dir() == expected
    assert admin_parser().parse_args(["set-password"]).data_dir == expected


def test_a_variable_set_for_the_services_reaches_all_three(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path / "chosen"))
    assert ApiSettings().data_dir == tmp_path / "chosen"
    assert vision_cli._data_dir() == tmp_path / "chosen"
    assert admin_parser().parse_args(["set-password"]).data_dir == tmp_path / "chosen"


# --- the data directory is private ------------------------------------------------------------


def test_a_new_data_directory_is_private_from_the_start(tmp_path: Path) -> None:
    from trafficpi.core.paths import ensure_private_dir

    folder = tmp_path / "a" / "data"
    ensure_private_dir(folder)
    assert oct(folder.stat().st_mode & 0o777) == "0o700"


def test_an_existing_data_directory_that_is_open_to_others_is_closed(tmp_path: Path) -> None:
    """It holds the password hash, the session key and pictures of the street."""
    from trafficpi.core.paths import ensure_private_dir

    folder = tmp_path / "data"
    folder.mkdir()
    folder.chmod(0o775)
    ensure_private_dir(folder)
    assert oct(folder.stat().st_mode & 0o777) == "0o700"


def test_opening_the_store_makes_the_directory_private(tmp_path: Path) -> None:
    from trafficpi.db.store import open_store

    folder = tmp_path / "data"
    folder.mkdir()
    folder.chmod(0o755)
    open_store(folder)
    assert oct(folder.stat().st_mode & 0o777) == "0o700"


def test_a_directory_that_is_not_ours_is_left_as_it_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the owner can change a directory's mode; trying to would just raise."""
    import os

    from trafficpi.core.paths import ensure_private_dir

    folder = tmp_path / "data"
    folder.mkdir()
    folder.chmod(0o755)
    monkeypatch.setattr(os, "getuid", lambda: folder.stat().st_uid + 1)
    ensure_private_dir(folder)  # no error, and no change
    assert oct(folder.stat().st_mode & 0o777) == "0o755"
