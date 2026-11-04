import logging
from pathlib import Path

import pytest

from trafficpi.vision import detector
from trafficpi.vision.detector import DEFAULT_MODEL, FALLBACK_MODEL, find_hef


@pytest.fixture(autouse=True)
def a_hailo_8l(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(detector, "hailo_architecture", lambda: "HAILO8L")


@pytest.fixture
def folders(tmp_path: Path) -> tuple[Path, Path]:
    packaged, fetched = tmp_path / "packaged", tmp_path / "fetched"
    packaged.mkdir()
    fetched.mkdir()
    return packaged, fetched


def test_a_model_from_the_package_is_found(folders: tuple[Path, Path]) -> None:
    file = folders[0] / "yolov6n_h8l.hef"
    file.touch()
    assert find_hef("yolov6n", model_dirs=folders) == file


def test_the_default_model_is_found_where_it_was_fetched_to(folders: tuple[Path, Path]) -> None:
    (folders[0] / f"{FALLBACK_MODEL}_h8l.hef").touch()
    file = folders[1] / f"{DEFAULT_MODEL}_h8l.hef"
    file.touch()
    assert find_hef(model_dirs=folders) == file


def test_with_the_default_model_not_fetched_the_fallback_is_used_and_the_log_says_how_to_fetch_it(
    folders: tuple[Path, Path], caplog: pytest.LogCaptureFixture
) -> None:
    file = folders[0] / f"{FALLBACK_MODEL}_h8l.hef"
    file.touch()
    with caplog.at_level(logging.WARNING, logger="trafficpi.vision"):
        assert find_hef(model_dirs=folders) == file
    assert "scripts/fetch_model.sh" in caplog.text


def test_any_other_model_that_is_missing_is_an_error_that_says_what_there_is(
    folders: tuple[Path, Path],
) -> None:
    (folders[1] / "yolov6n_h8l.hef").touch()
    with pytest.raises(RuntimeError, match=r"yolov99_h8l\.hef not found.*yolov6n_h8l\.hef"):
        find_hef("yolov99", model_dirs=folders)


def test_with_no_model_at_all_it_says_so_and_does_not_go_round_in_circles(
    folders: tuple[Path, Path],
) -> None:
    with pytest.raises(RuntimeError, match="not found"):
        find_hef(model_dirs=folders)


def test_an_unknown_accelerator_is_an_error(
    monkeypatch: pytest.MonkeyPatch, folders: tuple[Path, Path]
) -> None:
    monkeypatch.setattr(detector, "hailo_architecture", lambda: None)
    with pytest.raises(RuntimeError, match="Hailo architecture"):
        find_hef(model_dirs=folders)
