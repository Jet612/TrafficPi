"""The stop endpoints: what vehicles did at stop signs, and what a person made of it."""

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.calibrations import CALIBRATION, OTHER, name_of
from trafficpi.api.app import _stop_out, create_app
from trafficpi.api.auth import hash_password
from trafficpi.api.settings import ApiSettings
from trafficpi.api.vision_client import VisionClient
from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.store import EventRow, Store
from trafficpi.vision.rules import CountEvent, StopEvent

PASSWORD = "correct horse battery"
BASE = 1_760_000_000_000
HOUR = 3_600_000


def stop(outcome: str, sign: str = "elm", label: str = "car") -> StopEvent:
    return StopEvent(0, 7, 2, label, sign, outcome, hold_s=1.6, min_speed=0.05, observed_s=4.2)  # type: ignore[arg-type]


@pytest.fixture
def store() -> Store:
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    s = Store(engine)
    s.ensure_camera()
    s.create_user("admin", hash_password(PASSWORD))
    return s


@pytest.fixture
def app(tmp_path: Path, store: Store) -> FastAPI:
    vision = VisionClient(
        "http://vision", transport=httpx.MockTransport(lambda request: httpx.Response(404))
    )
    return create_app(
        ApiSettings(data_dir=tmp_path, dashboard_dir=None), store=store, vision=vision
    )


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url="http://testserver") as c:
        assert (
            c.post("/api/auth/login", json={"username": "admin", "password": PASSWORD}).status_code
            == 204
        )
        yield c


RANGE = {"from_ms": BASE, "to_ms": BASE + HOUR}


def test_the_stop_endpoints_need_a_login(app: FastAPI) -> None:
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/stops", params=RANGE).status_code == 401
        assert anonymous.get("/api/stops/summary", params=RANGE).status_code == 401
        assert anonymous.put("/api/stops/1/review", json={"state": "confirmed"}).status_code == 401


def test_a_stop_comes_back_with_what_was_seen_and_nothing_reviewed(
    client: TestClient, store: Store
) -> None:
    store.record_stop(stop("rolling", label="truck"), BASE + 5_000)
    (row,) = client.get("/api/stops", params=RANGE).json()
    assert row == {
        "id": row["id"],
        "t": BASE + 5_000,
        "sign": "elm",
        "label": "truck",
        "outcome": "rolling",
        "hold_s": 1.6,
        "min_speed": 0.05,
        "observed_s": 4.2,
        "min_speed_ms": None,  # the camera was not calibrated
        "max_speed_ms": None,
        "line_speed_ms": None,
        "calibrated": False,
        "review": None,
        "reviewed_at": None,
        "snapshot_url": None,
        "clip_url": None,
    }


def test_a_pass_measured_on_a_calibrated_road_comes_back_with_its_speeds(
    client: TestClient, store: Store
) -> None:
    measured = StopEvent(
        0,
        7,
        2,
        "car",
        "elm",
        "rolling",
        hold_s=0.2,
        min_speed=0.5,
        observed_s=4.0,
        min_speed_ms=1.5,
        max_speed_ms=9.75,
        line_speed_ms=0.0,
        calibration="ab12cd34",
    )
    store.record_stop(measured, BASE + 5_000)
    (row,) = client.get("/api/stops", params=RANGE).json()
    assert (row["min_speed_ms"], row["max_speed_ms"], row["line_speed_ms"]) == (1.5, 9.75, 0.0)


def test_a_pass_says_whether_its_speeds_are_of_the_calibration_in_force(
    client: TestClient, store: Store
) -> None:
    """The pages show a measured speed only if it was measured under the calibration saved now;
    a pass of an earlier calibration still carries its speeds, and says it is not."""

    def measured(calibration: str, at: int) -> int:
        event = StopEvent(
            0, 7, 2, "car", "elm", "rolling", 0.2, 0.5, 4.0,
            min_speed_ms=1.5, max_speed_ms=9.75, line_speed_ms=3.0, calibration=calibration,
        )  # fmt: skip
        return store.record_stop(event, BASE + at)

    def flags() -> dict[int, bool]:
        return {
            row["id"]: row["calibrated"] for row in client.get("/api/stops", params=RANGE).json()
        }

    old = measured(name_of(OTHER), 1_000)
    now_ = measured(name_of(CALIBRATION), 2_000)
    estimate = store.record_stop(stop("rolling"), BASE + 3_000)  # no speeds at all
    assert flags() == {old: False, now_: False, estimate: False}  # no calibration is saved yet
    store.set_profile({"calibration": CALIBRATION})
    assert flags() == {old: False, now_: True, estimate: False}
    rows = {row["id"]: row for row in client.get("/api/stops", params=RANGE).json()}
    assert rows[old]["min_speed_ms"] == 1.5, (
        "the speeds of the earlier calibration are still served"
    )
    reviewed = client.put(f"/api/stops/{now_}/review", json={"state": "confirmed"}).json()
    assert reviewed["calibrated"] is True
    store.set_profile({"calibration": OTHER})  # corrected since: the first is now the one in force
    assert flags() == {old: True, now_: False, estimate: False}
    store.set_profile({})  # taken away: nothing is calibrated
    assert flags() == {old: False, now_: False, estimate: False}


def test_a_speed_that_was_not_measured_is_none_not_minus_one(
    client: TestClient, store: Store
) -> None:
    partly = StopEvent(
        0, 7, 2, "car", "elm", "did_not_stop", 0.0, 6.0, 3.0, max_speed_ms=9.0, calibration="x"
    )
    store.record_stop(partly, BASE + 5_000)
    (row,) = client.get("/api/stops", params=RANGE).json()
    assert (row["min_speed_ms"], row["max_speed_ms"], row["line_speed_ms"]) == (None, 9.0, None)


def test_stops_are_listed_newest_first_filtered_and_paged(client: TestClient, store: Store) -> None:
    for i, outcome in enumerate(["stopped", "rolling", "did_not_stop", "indeterminate", "rolling"]):
        store.record_stop(stop(outcome, sign="oak" if i == 2 else "elm"), BASE + i * 1_000)

    def ask(**params: object) -> list[dict[str, object]]:
        return client.get("/api/stops", params={**RANGE, **params}).json()  # type: ignore[no-any-return]

    assert [r["outcome"] for r in ask()] == [
        "rolling",
        "indeterminate",
        "did_not_stop",
        "rolling",
        "stopped",
    ]
    assert [r["outcome"] for r in ask(outcome="rolling")] == ["rolling", "rolling"]
    assert [r["outcome"] for r in ask(outcome="rolling, did_not_stop")] == [
        "rolling",
        "did_not_stop",
        "rolling",
    ]
    assert [r["sign"] for r in ask(sign="oak")] == ["oak"]
    page = ask(limit=2)
    assert len(page) == 2
    assert [r["id"] for r in ask(limit=2, before_id=page[-1]["id"])] == [
        r["id"] for r in ask()[2:4]
    ]
    assert ask(from_ms=BASE + 10_000, to_ms=BASE + 20_000) == []


def test_a_review_is_made_filtered_by_and_taken_back(client: TestClient, store: Store) -> None:
    first = store.record_stop(stop("rolling"), BASE + 1_000)
    second = store.record_stop(stop("rolling"), BASE + 2_000)
    reviewed = client.put(f"/api/stops/{first}/review", json={"state": "false_positive"})
    assert reviewed.status_code == 200
    body = reviewed.json()
    assert (body["id"], body["review"]) == (first, "false_positive") and body[
        "reviewed_at"
    ] is not None

    def ids(review: str) -> list[int]:
        found = client.get("/api/stops", params={**RANGE, "review": review}).json()
        return [row["id"] for row in found]

    assert (
        ids("false_positive") == [first]
        and ids("unreviewed") == [second]
        and ids("confirmed") == []
    )
    # "counted" leaves out the false alarm and keeps the pass nobody has reviewed; none: every pass
    assert ids("counted") == [second]
    assert sorted(row["id"] for row in client.get("/api/stops", params=RANGE).json()) == [
        first,
        second,
    ]
    third = store.record_stop(stop("rolling"), BASE + 3_000)
    client.put(f"/api/stops/{third}/review", json={"state": "confirmed"})
    assert ids("counted") == [third, second]
    undone = client.put(f"/api/stops/{first}/review", json={"state": None}).json()
    assert (undone["review"], undone["reviewed_at"]) == (None, None)
    assert sorted(ids("unreviewed")) == [first, second]
    assert sorted(ids("counted")) == [first, second, third]


def test_the_summary_counts_by_outcome_and_review(client: TestClient, store: Store) -> None:
    a = store.record_stop(stop("rolling"), BASE + 1_000)
    store.record_stop(stop("rolling"), BASE + 2_000)
    store.record_stop(stop("stopped"), BASE + 3_000)
    store.record_stop(stop("stopped"), BASE + 2 * HOUR)  # another hour
    client.put(f"/api/stops/{a}/review", json={"state": "confirmed"})
    summary = client.get("/api/stops/summary", params=RANGE).json()
    assert (summary["from_ms"], summary["to_ms"]) == (BASE, BASE + HOUR)
    assert [o["outcome"] for o in summary["outcomes"]] == [
        "stopped",
        "rolling",
        "did_not_stop",
        "indeterminate",
    ]
    by_outcome = {o["outcome"]: o for o in summary["outcomes"]}
    assert by_outcome["rolling"] == {
        "outcome": "rolling",
        "total": 2,
        "confirmed": 1,
        "false_positive": 0,
        "unreviewed": 1,
    }
    assert by_outcome["stopped"]["total"] == 1 and by_outcome["did_not_stop"]["total"] == 0


@pytest.mark.parametrize(
    "params",
    [
        {"from_ms": 10, "to_ms": 10},
        {"from_ms": 10, "to_ms": 5},
        {"from_ms": 0, "to_ms": 367 * 86_400_000},
        {"from_ms": -1, "to_ms": 5},
        {"from_ms": "soon", "to_ms": 5},
        {"from_ms": 0},
        {**RANGE, "outcome": "late"},
        {**RANGE, "outcome": ""},
        {**RANGE, "review": "maybe"},
        {**RANGE, "limit": 0},
        {**RANGE, "limit": 501},
    ],
)
def test_a_question_that_makes_no_sense_is_refused(
    client: TestClient, params: dict[str, object]
) -> None:
    assert client.get("/api/stops", params=params).status_code == 422  # type: ignore[arg-type]
    if "limit" not in params and "review" not in params and "outcome" not in params:
        assert client.get("/api/stops/summary", params=params).status_code == 422  # type: ignore[arg-type]


def test_an_unknown_outcome_is_refused_with_the_ones_there_are(client: TestClient) -> None:
    detail = client.get("/api/stops", params={**RANGE, "outcome": "late"}).json()["detail"]
    assert "unknown outcome 'late'" in detail and "did_not_stop" in detail


def test_what_cannot_be_reviewed_is_refused(client: TestClient, store: Store) -> None:
    counted = store.record_count(
        CountEvent(0, 1, 2, "car", "main", 1),
        BASE,
    )
    assert (
        client.put(f"/api/stops/{counted}/review", json={"state": "confirmed"}).status_code == 404
    )
    assert client.put("/api/stops/99999/review", json={"state": "confirmed"}).status_code == 404
    stopped = store.record_stop(stop("rolling"), BASE)
    assert client.put(f"/api/stops/{stopped}/review", json={"state": "maybe"}).status_code == 422
    assert client.put(f"/api/stops/{stopped}/review", json={}).status_code == 422
    assert client.get("/api/stops", params=RANGE).json()[0]["review"] is None  # nothing changed


def test_a_stop_event_without_its_details_is_shown_as_one_that_could_not_be_judged(
    tmp_path: Path,
) -> None:
    """Such a row should not exist; if one does (a damaged database), the list must not fail."""
    row = EventRow(5, BASE, "stop", 7, "car", "elm", None, None, None)
    out = _stop_out(row, tmp_path, None)
    assert (out.outcome, out.hold_s, out.min_speed, out.observed_s) == (
        "indeterminate",
        0.0,
        -1.0,
        0.0,
    )
    assert (out.review, out.sign, out.label) == (None, "elm", "car")


def test_the_events_list_still_has_both_kinds_for_those_who_ask_for_both(
    client: TestClient, store: Store
) -> None:
    store.record_stop(stop("rolling"), BASE)
    assert [e["type"] for e in client.get("/api/events").json()] == ["stop"]
    assert client.get("/api/events", params={"type": "count"}).json() == []
