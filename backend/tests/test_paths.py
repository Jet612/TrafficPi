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
