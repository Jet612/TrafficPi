"""Stop signs are judged only in the hours the owner sets.

A drive is placed at a chosen date and time of day through the service's timestamp hook, so that
"noon" and "night" are the same on every machine and in every season of the year.
"""

import logging
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import TypedDict
from zoneinfo import ZoneInfo

import pytest

from tests.fakes import (
    FULL_STOP,
    STOP_SIGN,
    ScriptedDetector,
    ScriptedSource,
    car_driving,
)
from tests.test_evidence import FakeEncoder
from trafficpi.core.clock import WallClock
from trafficpi.core.stop_hours import StopHours, TimeRange, parse_clock
from trafficpi.core.types import Detection
from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.store import Store
from trafficpi.vision.evidence import EvidenceJob, EvidenceRecorder
from trafficpi.vision.service import ServiceConfig, VisionService
from trafficpi.vision.shutdown import StopRequest

NEW_YORK = ZoneInfo("America/New_York")


class Place(TypedDict):
    latitude: float
    longitude: float


NYC: Place = {"latitude": 40.7128, "longitude": -74.006}
# The window in New York on 2026-10-09 is 06:31 to 18:55 (sunrise 07:01, sunset 18:25).
NOON = (2026, 10, 9, 12, 0)  # a Friday
NIGHT = (2026, 10, 9, 23, 0)
SATURDAY_NOON = (2026, 10, 10, 12, 0)
LINE = {"id": "main", "name": "Main", "a": [0.7, 0.9], "b": [0.7, 0.3]}  # past the sign


def ms(when: tuple[int, int, int, int, int]) -> int:
    return int(datetime(*when, tzinfo=NEW_YORK).timestamp() * 1000)


class StubClock(WallClock):
    """A wall clock that only moves when the test says."""

    def __init__(self, now: int) -> None:
        super().__init__()
        self.now = now

    def now_ms(self) -> int:
        return self.now


class Asked(EvidenceRecorder):
    """A recorder that remembers what it was asked for."""

    def __init__(self, *args, **kw) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kw)
        self.jobs: list[EvidenceJob] = []

    def request(self, job: EvidenceJob) -> None:
        self.jobs.append(job)
        super().request(job)


@pytest.fixture
def store() -> Store:
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    s = Store(engine)
    s.ensure_camera()
    s.set_profile({"lines": [LINE], "stop_signs": [STOP_SIGN | {"name": "Elm Street"}]})
    s.set_setting("timezone", "America/New_York")
    return s


@pytest.fixture
def recorder(tmp_path: Path) -> Iterator[Asked]:
    recorder = Asked(tmp_path, encoder=FakeEncoder(), max_waiting=2_000)
    yield recorder
    recorder.close()


def drive_at(
    store: Store,
    when: tuple[int, int, int, int, int],
    *,
    script: list[list[Detection]] | None = None,
    on_read=None,  # type: ignore[no-untyped-def]
    **kw,  # type: ignore[no-untyped-def]
) -> VisionService:
    """A car stops at the sign (and drives on past the counting line) at the given time."""
    start = ms(when)
    source = ScriptedSource(
        script if script is not None else car_driving([*FULL_STOP, (1.0, 0.15, 0.15)]),
        on_read=on_read,
    )
    source.frame_size = (640, 360)
    service = VisionService(
        source,
        ScriptedDetector(source),
        store,
        timestamp_ms=lambda frame: start + frame.timestamp_ns // 1_000_000,
        **kw,
    )
    service.run(StopRequest())
    return service


def outcomes(store: Store) -> list[str]:
    rows = store.recent_events(type="stop", limit=100)
    return [(r.details or {})["outcome"] for r in reversed(rows)]


def counted(store: Store) -> int:
    return sum(m.count for m in store.minute_counts(0, 2**50))


# --- the control ------------------------------------------------------------------------------


def test_with_no_limit_a_pass_in_the_dead_of_night_is_judged_as_it_always_was(
    store: Store,
) -> None:
    service = drive_at(store, NIGHT)
    assert outcomes(store) == ["stopped"]
    assert service.stops_skipped == 0


# --- the daylight limit ------------------------------------------------------------------------


def test_in_daylight_a_pass_at_noon_is_judged_and_one_at_night_is_not(store: Store) -> None:
    store.set_stop_hours(StopHours(daylight=True, **NYC))
    by_day = drive_at(store, NOON)
    assert outcomes(store) == ["stopped"] and by_day.stops_skipped == 0
    by_night = drive_at(store, NIGHT)
    assert outcomes(store) == ["stopped"]  # still the one from noon: none was added at night
    assert by_night.stops_skipped == 1


def test_a_pass_turned_away_leaves_nothing_behind_not_a_row_not_a_picture_not_a_count(
    store: Store, tmp_path: Path, recorder: Asked
) -> None:
    store.set_stop_hours(StopHours(daylight=True, **NYC))
    service = drive_at(store, NIGHT, data_dir=tmp_path, evidence=recorder)
    assert store.recent_events(type="stop") == []
    assert recorder.jobs == []  # no picture and no clip were asked for
    assert service.stops_skipped == 1
    assert service.events_recorded == 1  # only the vehicle at the counting line


def test_the_same_pass_in_daylight_does_ask_for_evidence_when_it_needs_a_look(
    store: Store, tmp_path: Path, recorder: Asked
) -> None:
    # The control for the test above: the recorder does get a job when the hours allow the pass.
    store.set_stop_hours(StopHours(daylight=True, **NYC))
    rolling = [(1.5, 0.15, 0.15), (1.0, 0.15, 0.04), (1.0, 0.04, 0.04), (1.0, 0.04, 0.15)]
    drive_at(store, NOON, script=car_driving(rolling), data_dir=tmp_path, evidence=recorder)
    assert outcomes(store) == ["rolling"]
    assert len(recorder.jobs) == 1


def test_the_vehicles_are_still_counted_at_the_counting_lines_in_the_dark(store: Store) -> None:
    store.set_stop_hours(StopHours(daylight=True, **NYC))
    drive_at(store, NIGHT)
    assert counted(store) == 1  # the drive went on past the counting line
    assert outcomes(store) == []


def test_trails_are_still_saved_in_the_dark(store: Store) -> None:
    store.set_stop_hours(StopHours(daylight=True, **NYC))
    service = drive_at(store, NIGHT)
    assert service.trails_recorded == 1


# --- the set hours -----------------------------------------------------------------------------


def test_set_hours_on_chosen_days_judge_a_friday_and_not_a_saturday(store: Store) -> None:
    store.set_stop_hours(
        StopHours(
            clock=True,
            ranges=(TimeRange(parse_clock("07:00"), parse_clock("19:00")),),
            days=frozenset(range(5)),
        )
    )
    drive_at(store, NOON)
    assert outcomes(store) == ["stopped"]
    saturday = drive_at(store, SATURDAY_NOON)
    assert outcomes(store) == ["stopped"]
    assert saturday.stops_skipped == 1


def test_the_set_hours_are_on_the_clock_of_the_zone_the_owner_chose(store: Store) -> None:
    store.set_stop_hours(
        StopHours(clock=True, ranges=(TimeRange(parse_clock("07:00"), parse_clock("19:00")),))
    )
    store.set_setting("timezone", "Asia/Tokyo")  # noon in New York is 01:00 the next day in Tokyo
    drive_at(store, NOON)
    assert outcomes(store) == []
    store.set_setting("timezone", "America/New_York")
    drive_at(store, NOON)
    assert outcomes(store) == ["stopped"]


def test_with_both_limits_a_pass_must_be_inside_both(store: Store) -> None:
    store.set_stop_hours(
        StopHours(
            daylight=True,
            clock=True,
            ranges=(TimeRange(parse_clock("09:00"), parse_clock("20:00")),),
            **NYC,
        )
    )
    drive_at(store, (2026, 10, 9, 8, 0))  # daylight, but before the set hours
    drive_at(store, (2026, 10, 9, 19, 30))  # the set hours, but after dark
    assert outcomes(store) == []
    drive_at(store, NOON)
    assert outcomes(store) == ["stopped"]


# --- changing the hours in a running service ---------------------------------------------------


def test_the_hours_are_read_again_on_a_reload_so_no_restart_is_needed(store: Store) -> None:
    script = [[] for _ in range(60)] + car_driving([*FULL_STOP, (1.0, 0.15, 0.15)])

    def switch_on_early(index: int) -> None:
        if index == 10:
            store.set_stop_hours(StopHours(daylight=True, **NYC))
            holder[0].request_reload()

    holder: list[VisionService] = []
    source = ScriptedSource(script, on_read=switch_on_early)
    source.frame_size = (640, 360)
    start = ms(NIGHT)
    service = VisionService(
        source,
        ScriptedDetector(source),
        store,
        timestamp_ms=lambda frame: start + frame.timestamp_ns // 1_000_000,
    )
    holder.append(service)
    service.run(StopRequest())
    assert outcomes(store) == []
    assert service.stops_skipped == 1


def test_switching_a_limit_off_in_a_running_service_judges_the_next_pass(store: Store) -> None:
    store.set_stop_hours(StopHours(daylight=True, **NYC))
    script = [[] for _ in range(60)] + car_driving([*FULL_STOP, (1.0, 0.15, 0.15)])
    holder: list[VisionService] = []

    def switch_off(index: int) -> None:
        if index == 10:
            store.set_stop_hours(StopHours(daylight=False, **NYC))
            holder[0].request_reload()

    source = ScriptedSource(script, on_read=switch_off)
    source.frame_size = (640, 360)
    start = ms(NIGHT)
    service = VisionService(
        source,
        ScriptedDetector(source),
        store,
        timestamp_ms=lambda frame: start + frame.timestamp_ns // 1_000_000,
    )
    holder.append(service)
    service.run(StopRequest())
    assert outcomes(store) == ["stopped"]
    assert service.stops_skipped == 0


# --- the journal -------------------------------------------------------------------------------


def test_the_journal_says_what_is_judged_when_the_service_starts(
    store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    store.set_stop_hours(StopHours(daylight=True, **NYC))
    with caplog.at_level(logging.INFO, logger="trafficpi.vision"):
        drive_at(store, NOON, script=[[] for _ in range(5)])
    assert "stop signs are judged only from 30 minutes before sunrise" in caplog.text
    assert "40.7" not in caplog.text and "74.0" not in caplog.text  # where the camera is stays out


def test_with_no_limit_the_journal_is_silent_about_it(
    store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="trafficpi.vision"):
        drive_at(store, NOON, script=[[] for _ in range(5)])
    assert "judged only" not in caplog.text and "paused" not in caplog.text


def test_the_journal_says_when_judging_pauses_for_the_night_and_when_it_resumes(
    store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    store.set_stop_hours(StopHours(daylight=True, **NYC))
    clock = StubClock(ms((2026, 10, 9, 18, 40)))
    jumps = {
        100: ms((2026, 10, 9, 19, 30)),  # after dark
        200: ms((2026, 10, 10, 6, 0)),  # before the window opens
        300: ms((2026, 10, 10, 7, 0)),  # inside it
    }

    def move_the_clock(index: int) -> None:
        if index in jumps:
            clock.now = jumps[index]

    with caplog.at_level(logging.INFO, logger="trafficpi.vision"):
        drive_at(
            store,
            NOON,
            script=[[] for _ in range(400)],
            on_read=move_the_clock,
            clock=clock,
            config=ServiceConfig(metrics_every_s=0),
        )
    lines = [r.getMessage() for r in caplog.records if "stop-sign judging" in r.getMessage()]
    assert len(lines) == 2, lines
    assert "paused" in lines[0] and "resumes" in lines[0] and "Sat" in lines[0]
    assert "resumed" in lines[1]


def test_a_reload_that_changes_nothing_does_not_say_it_all_again(
    store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    store.set_stop_hours(StopHours(daylight=True, **NYC))
    holder: list[VisionService] = []

    def reload_often(index: int) -> None:
        if index % 10 == 5 and holder:
            holder[0].request_reload()

    source = ScriptedSource([[] for _ in range(60)], on_read=reload_often)
    source.frame_size = (640, 360)
    service = VisionService(source, ScriptedDetector(source), store)
    holder.append(service)
    with caplog.at_level(logging.INFO, logger="trafficpi.vision"):
        service.run(StopRequest())
    assert caplog.text.count("stop signs are judged only") == 1


def test_a_limit_that_cannot_work_is_said_so_and_every_pass_is_judged(
    store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    store.set_setting("stop_hours_daylight", True)  # switched on, but no place was ever saved
    with caplog.at_level(logging.WARNING, logger="trafficpi.vision"):
        service = drive_at(store, NIGHT)
    assert "latitude and longitude" in caplog.text
    assert outcomes(store) == ["stopped"] and service.stops_skipped == 0


def test_the_settings_cannot_be_read_so_the_ones_in_use_stay_and_the_counting_goes_on(
    store: Store, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken() -> StopHours:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(store, "get_stop_hours", broken)
    with caplog.at_level(logging.ERROR, logger="trafficpi.vision"):
        service = drive_at(store, NIGHT)
    assert "could not read the stop-sign hours" in caplog.text
    assert outcomes(store) == ["stopped"]  # no limit was ever read, so none applies
    assert service.frames > 0


def test_a_failure_in_the_check_never_costs_a_pass_or_stops_the_counting(
    store: Store, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.set_stop_hours(StopHours(daylight=True, **NYC))

    def broken(*args, **kw):  # type: ignore[no-untyped-def]
        raise RuntimeError("the sun is broken")

    monkeypatch.setattr("trafficpi.vision.service.judging_at", broken)
    with caplog.at_level(logging.ERROR, logger="trafficpi.vision"):
        drive_at(store, NIGHT)
    assert outcomes(store) == ["stopped"]  # judged: it could not be said that it should not be
    assert "could not tell whether stop signs are being judged" in caplog.text
