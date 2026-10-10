"""CSV exports of the counts and of the passes at the stop signs (plan.md D62, M7)."""

import csv
import io
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response

from tests.calibrations import CALIBRATION, OTHER, name_of
from trafficpi.api import analytics, export
from trafficpi.api.app import create_app
from trafficpi.api.auth import hash_password
from trafficpi.api.settings import ApiSettings
from trafficpi.api.vision_client import VisionClient
from trafficpi.core.clock import WallClock
from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.store import Store
from trafficpi.vision.rules import CountEvent, StopEvent

PASSWORD = "correct horse battery"
NY = ZoneInfo("America/New_York")


def ms(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=NY).timestamp() * 1000)


NOW = ms("2026-10-09 12:30")  # a Friday
LINES = [
    {"id": "north", "name": "Main street", "a": [0.2, 0.2], "b": [0.2, 0.8]},
    {"id": "evil", "name": '=HYPERLINK("http://x")', "a": [0.8, 0.2], "b": [0.8, 0.8]},
]
SIGN = {
    "id": "elm",
    "name": 'Elm, "the" corner',
    "approach": [[0.1, 0.5], [0.3, 0.5], [0.3, 0.9], [0.1, 0.9]],
    "a": [0.1, 0.9],
    "b": [0.3, 0.9],
}


@pytest.fixture
def store() -> Store:
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    s = Store(engine)
    s.ensure_camera()
    s.create_user("admin", hash_password(PASSWORD))
    s.set_setting("timezone", "America/New_York")
    s.set_profile({"lines": LINES, "stop_signs": [SIGN], "calibration": CALIBRATION})
    return s


@pytest.fixture
def app(tmp_path: Path, store: Store) -> FastAPI:
    vision = VisionClient(
        "http://vision", transport=httpx.MockTransport(lambda request: httpx.Response(404))
    )
    made = create_app(
        ApiSettings(data_dir=tmp_path, dashboard_dir=None), store=store, vision=vision
    )
    made.state.ctx.clock = WallClock(wall_ns=lambda: NOW * 1_000_000)
    return made


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url="http://testserver") as c:
        assert (
            c.post("/api/auth/login", json={"username": "admin", "password": PASSWORD}).status_code
            == 204
        )
        yield c


def count(store: Store, at: str, line: str = "north", direction: int = 1, label: str = "car",
          speed_ms: float | None = None, calibration: str | None = None) -> None:  # fmt: skip
    event = CountEvent(0, 1, 2, label, line, direction)
    if speed_ms is not None:
        event = CountEvent(
            0, 1, 2, label, line, direction, speed_ms=speed_ms, calibration=calibration
        )
    store.record_count(event, ms(at))


def rows_of(response: Response) -> list[list[str]]:
    assert response.status_code == 200, response.text
    return list(csv.reader(io.StringIO(response.text)))


# --- counts ---------------------------------------------------------------------------------------


def test_the_counts_of_a_period_come_as_a_file_with_a_header_a_row_for_each_and_times_twice(
    client: TestClient, store: Store
) -> None:
    count(store, "2026-10-09 08:15:30", speed_ms=6.5, calibration=name_of(CALIBRATION))
    count(store, "2026-10-09 09:00", direction=-1, label="truck")
    response = client.get("/api/export/counts.csv", params={"days": 1})
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == (
        'attachment; filename="trafficpi-counts-2026-10-09-to-2026-10-09.csv"'
    )
    assert response.headers["cache-control"] == "no-store"
    rows = rows_of(response)
    assert rows[0] == export.COUNT_HEADER
    assert rows[1] == [
        "2026-10-09T12:15:30.000Z",  # 08:15:30 in New York (UTC-4) is 12:15:30 UTC
        "2026-10-09T08:15:30.000-04:00",
        "Main street",
        "forward",
        "car",
        "6.50",
        "yes",
    ]
    assert rows[2][2:] == ["Main street", "reverse", "truck", "", ""]
    assert len(rows) == 3


def test_a_speed_is_calibrated_only_if_it_was_measured_under_the_calibration_in_force(
    client: TestClient, store: Store
) -> None:
    count(store, "2026-10-09 08:00", speed_ms=5.0, calibration=name_of(OTHER))  # an earlier one
    count(store, "2026-10-09 08:01", speed_ms=5.0, calibration=name_of(CALIBRATION))
    rows = rows_of(client.get("/api/export/counts.csv", params={"days": 1}))
    assert [row[5:] for row in rows[1:]] == [["5.00", ""], ["5.00", "yes"]]


def test_the_period_is_the_data_pages_whole_local_days_and_all_time_is_everything(
    client: TestClient, store: Store
) -> None:
    count(store, "2026-10-02 23:59")  # the day before a week ago
    count(store, "2026-10-03 00:00")  # the first minute of the 7 days
    count(store, "2026-10-09 23:59")  # the last of today
    count(store, "2026-10-10 00:00")  # tomorrow: not yet
    week = rows_of(client.get("/api/export/counts.csv", params={"days": 7}))
    assert [row[1][:16] for row in week[1:]] == ["2026-10-03T00:00", "2026-10-09T23:59"]
    everything = rows_of(client.get("/api/export/counts.csv", params={"days": 0}))
    assert len(everything) == 1 + 3
    assert (
        "2026-10-02-to"
        in client.get("/api/export/counts.csv", params={"days": 0}).headers["content-disposition"]
    )


def test_a_name_that_a_spreadsheet_would_take_for_a_formula_is_made_plain_text(
    client: TestClient, store: Store
) -> None:
    count(store, "2026-10-09 08:00", line="evil")
    count(store, "2026-10-09 08:01", line="gone-line")  # a deleted line is its id
    rows = rows_of(client.get("/api/export/counts.csv", params={"days": 1}))
    assert rows[1][2] == '\'=HYPERLINK("http://x")'
    assert rows[2][2] == "gone-line"
    assert export.safe("-1+1") == "'-1+1" and export.safe("@SUM(A1)") == "'@SUM(A1)"
    assert export.safe("+1") == "'+1" and export.safe("a=b") == "a=b" and export.safe(None) == ""


def test_an_empty_period_is_a_file_with_only_its_header(client: TestClient) -> None:
    assert rows_of(client.get("/api/export/counts.csv", params={"days": 1})) == [
        export.COUNT_HEADER
    ]


def test_more_rows_than_a_page_all_come_in_order(
    client: TestClient, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(export, "PAGE", 3)
    for minute in range(8):  # three pages: 3, 3 and 2
        count(store, f"2026-10-09 08:0{minute}")
    rows = rows_of(client.get("/api/export/counts.csv", params={"days": 1}))
    assert [row[1][11:16] for row in rows[1:]] == [f"08:0{minute}" for minute in range(8)]


def test_exactly_a_page_of_rows_needs_no_extra_page_and_loses_nothing(
    client: TestClient, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(export, "PAGE", 4)
    for minute in range(4):
        count(store, f"2026-10-09 08:0{minute}")
    assert len(rows_of(client.get("/api/export/counts.csv", params={"days": 1}))) == 5


# --- passes ---------------------------------------------------------------------------------------


def make_pass(store: Store, at: str, outcome: str, **kw: object) -> int:
    event = StopEvent(
        0,
        7,
        2,
        "car",
        "elm",
        outcome,  # type: ignore[arg-type]
        hold_s=1.6,
        min_speed=0.05,
        observed_s=4.2,
        **kw,  # type: ignore[arg-type]
    )
    return store.record_stop(event, ms(at))


def test_the_passes_come_with_what_they_did_how_long_and_how_fast_and_what_a_person_made_of_them(
    client: TestClient, store: Store
) -> None:
    first = make_pass(store, "2026-10-09 09:00", "rolling", min_speed_ms=1.8, max_speed_ms=6.2,
                      line_speed_ms=3.0, calibration=name_of(CALIBRATION))  # fmt: skip
    second = make_pass(store, "2026-10-09 09:30", "did_not_stop")
    third = make_pass(store, "2026-10-09 10:00", "stopped")
    store.review_stop(first, "confirmed")
    store.review_stop(second, "false_positive")
    assert third
    response = client.get("/api/export/passes.csv", params={"days": 1})
    assert response.headers["content-disposition"] == (
        'attachment; filename="trafficpi-stop-passes-2026-10-09-to-2026-10-09.csv"'
    )
    rows = rows_of(response)
    assert rows[0] == export.PASS_HEADER
    assert rows[1] == [
        "2026-10-09T13:00:00.000Z",
        "2026-10-09T09:00:00.000-04:00",
        'Elm, "the" corner',  # the comma and the quotes survive the file's own quoting
        "car",
        "rolled through",
        "1.6",
        "4.2",
        "1.80",
        "6.20",
        "3.00",
        "yes",
        "confirmed",
    ]
    assert rows[2][4] == "did not stop" and rows[2][-1] == "false alarm"  # kept in, and marked
    assert rows[2][7:11] == ["", "", "", ""]  # no speed was measured
    assert rows[3][4] == "stopped" and rows[3][-1] == ""


def test_a_stored_outcome_nobody_knows_is_unclear_and_a_damaged_number_is_an_empty_cell(
    client: TestClient, store: Store
) -> None:
    from trafficpi.db.store import Event

    event_id = make_pass(store, "2026-10-09 09:00", "rolling")
    with store._session() as session:  # a damaged row: details that are not what they should be
        damaged = session.get(Event, event_id)
        assert damaged is not None
        damaged.details = {"outcome": "from-the-future", "hold_s": "long"}
    row = rows_of(client.get("/api/export/passes.csv", params={"days": 1}))[1]
    assert row[4] == "unclear" and row[5] == "" and row[6] == ""


# --- who may ask ----------------------------------------------------------------------------------


def test_a_visitor_who_is_not_signed_in_gets_neither_file(app: FastAPI) -> None:
    with TestClient(app, base_url="http://testserver") as anonymous:
        assert anonymous.get("/api/export/counts.csv").status_code == 401
        assert anonymous.get("/api/export/passes.csv").status_code == 401


@pytest.mark.parametrize("days", ["-1", str(analytics.MAX_DAYS + 1), "week", ""])
def test_a_period_that_makes_no_sense_is_refused(client: TestClient, days: str) -> None:
    assert client.get("/api/export/counts.csv", params={"days": days}).status_code == 422
    assert client.get("/api/export/passes.csv", params={"days": days}).status_code == 422
