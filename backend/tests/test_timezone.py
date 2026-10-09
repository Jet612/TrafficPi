import pytest

from trafficpi.core import clock
from trafficpi.core.clock import resolve_timezone


def test_a_name_gives_that_zone() -> None:
    assert str(resolve_timezone("Europe/Paris")) == "Europe/Paris"
    assert str(resolve_timezone("UTC")) == "UTC"


def test_no_name_gives_the_machines_own_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clock, "system_timezone_name", lambda: "Asia/Tokyo")
    for nothing in (None, ""):
        assert str(resolve_timezone(nothing)) == "Asia/Tokyo"


@pytest.mark.parametrize("name", ["Mars/Olympus", "../../etc/passwd", "not a zone", " "])
def test_a_zone_that_does_not_exist_gives_utc_not_a_crash(name: str) -> None:
    assert str(resolve_timezone(name)) == "UTC"


def test_something_stored_that_is_not_text_is_treated_as_no_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(clock, "system_timezone_name", lambda: "Asia/Tokyo")
    for odd in (7, ["UTC"], {"zone": "UTC"}, True):
        assert str(resolve_timezone(odd)) == "Asia/Tokyo"
