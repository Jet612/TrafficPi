"""The Data page's endpoints: stop-sign shares and traffic over local days (plan.md D25, M7)."""

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.calibrations import CALIBRATION, OTHER, name_of
from trafficpi.api.app import create_app
from trafficpi.api.auth import hash_password
from trafficpi.api.settings import ApiSettings
from trafficpi.api.vision_client import VisionClient
from trafficpi.core.clock import WallClock
from trafficpi.core.stop_hours import StopHours, TimeRange
from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.store import Store
from trafficpi.vision.rules import CountEvent, StopEvent

PASSWORD = "correct horse battery"
NY = ZoneInfo("America/New_York")


def ms(text: str) -> int:
    """ms since the epoch of a New York wall-clock time like '2026-10-09 12:30'."""
    return int(datetime.fromisoformat(text).replace(tzinfo=NY).timestamp() * 1000)


NOW = ms("2026-10-09 12:30")  # a Friday


def stop(outcome: str, sign: str = "elm", label: str = "car", hold_s: float = 1.6) -> StopEvent:
    return StopEvent(0, 7, 2, label, sign, outcome, hold_s=hold_s, min_speed=0.5, observed_s=4.2)  # type: ignore[arg-type]


@pytest.fixture
def store() -> Store:
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    s = Store(engine)
    s.ensure_camera()
    s.create_user("admin", hash_password(PASSWORD))
    s.set_setting("timezone", "America/New_York")
    return s


@pytest.fixture
def app(tmp_path: Path, store: Store) -> FastAPI:
    vision = VisionClient(
        "http://vision", transport=httpx.MockTransport(lambda request: httpx.Response(404))
    )
    made = create_app(
        ApiSettings(data_dir=tmp_path, dashboard_dir=None), store=store, vision=vision
    )
    made.state.ctx.clock = WallClock(wall_ns=lambda: NOW * 1_000_000)  # the day is a fixed one
    return made


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url="http://testserver") as c:
        assert (
            c.post("/api/auth/login", json={"username": "admin", "password": PASSWORD}).status_code
            == 204
        )
        yield c


def seed_week(store: Store) -> None:
    """Five passes in the week up to now, one in the week before it, one long before."""
    store.record_stop(stop("stopped", hold_s=2.0), ms("2026-10-09 09:00"))
    store.record_stop(stop("rolling", label="truck"), ms("2026-10-09 09:10"))
    store.record_stop(stop("did_not_stop", sign="oak"), ms("2026-10-08 08:05"))
    store.record_stop(stop("stopped", hold_s=3.0), ms("2026-10-05 17:30"))
    store.record_stop(stop("indeterminate", label="bicycle"), ms("2026-10-03 00:00"))  # day 1
    store.record_stop(stop("stopped"), ms("2026-09-30 10:00"))  # the week before
    store.record_stop(stop("rolling"), ms("2026-09-20 10:00"))  # before that: in neither


def test_the_data_endpoints_need_a_login(app: FastAPI) -> None:
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/stats/stops").status_code == 401
        assert anonymous.get("/api/stats/traffic").status_code == 401


@pytest.mark.parametrize("path", ["/api/stats/stops", "/api/stats/traffic"])
@pytest.mark.parametrize("days", ["-1", "367", "x", "1.5", ""])
def test_the_number_of_days_must_be_from_0_to_366(client: TestClient, path: str, days: str) -> None:
    assert client.get(path, params={"days": days}).status_code == 422
    assert client.get(path, params={"days": "366"}).status_code == 200
    assert client.get(path, params={"days": "0"}).status_code == 200


def test_a_stop_sign_id_cannot_be_absurdly_long(client: TestClient) -> None:
    assert client.get("/api/stats/stops", params={"sign": "x" * 65}).status_code == 422


def test_a_week_of_passes_is_summed_and_compared_with_the_week_before(
    client: TestClient, store: Store
) -> None:
    seed_week(store)
    body = client.get("/api/stats/stops", params={"days": 7}).json()
    assert (body["timezone"], body["units"], body["days"], body["all_time"]) == (
        "America/New_York",
        "mph",
        7,
        False,
    )
    assert (body["from_ms"], body["to_ms"]) == (ms("2026-10-03 00:00"), ms("2026-10-10 00:00"))
    assert body["totals"] == {"stopped": 2, "rolling": 1, "did_not_stop": 1, "indeterminate": 1}
    assert body["previous"] == {"stopped": 1, "rolling": 0, "did_not_stop": 0, "indeterminate": 0}
    assert [d["date"] for d in body["by_day"]] == [
        "2026-10-03",
        "2026-10-04",
        "2026-10-05",
        "2026-10-06",
        "2026-10-07",
        "2026-10-08",
        "2026-10-09",
    ]
    assert body["by_day"][0]["indeterminate"] == 1  # the stroke of midnight belongs to its day
    assert body["by_hour"][9]["stopped"] == 1 and body["by_hour"][9]["rolling"] == 1
    assert [k["label"] for k in body["by_kind"]] == ["car", "bicycle", "truck"]
    assert [s["sign"] for s in body["by_sign"]] == ["elm", "oak"]
    assert body["timing"]["stopped_hold_s"] == 2.5
    assert body["review"] == {"confirmed": 0, "false_alarms": 0, "waiting": 3}


def test_a_single_day_is_today_and_is_compared_with_yesterday(
    client: TestClient, store: Store
) -> None:
    seed_week(store)
    body = client.get("/api/stats/stops", params={"days": 1}).json()
    assert body["totals"] == {"stopped": 1, "rolling": 1, "did_not_stop": 0, "indeterminate": 0}
    assert body["previous"] == {"stopped": 0, "rolling": 0, "did_not_stop": 1, "indeterminate": 0}
    assert len(body["by_day"]) == 1


def test_one_stop_sign_can_be_asked_about(client: TestClient, store: Store) -> None:
    seed_week(store)
    body = client.get("/api/stats/stops", params={"days": 7, "sign": "oak"}).json()
    assert body["sign"] == "oak"
    assert body["totals"] == {"stopped": 0, "rolling": 0, "did_not_stop": 1, "indeterminate": 0}
    assert [s["sign"] for s in body["by_sign"]] == ["oak"]
    assert body["previous"]["stopped"] == 0  # the week before had only elm's


def test_false_alarms_are_left_out_of_the_shares_and_counted_apart(
    client: TestClient, store: Store
) -> None:
    seed_week(store)
    (miss,) = store.stops_between(ms("2026-10-08 00:00"), ms("2026-10-09 00:00"))
    store.review_stop(miss.id, "false_positive")
    (hold,) = store.stops_between(ms("2026-10-09 09:10"), ms("2026-10-09 09:11"))
    store.review_stop(hold.id, "confirmed")
    body = client.get("/api/stats/stops", params={"days": 7}).json()
    assert body["totals"] == {"stopped": 2, "rolling": 1, "did_not_stop": 0, "indeterminate": 1}
    assert body["review"] == {"confirmed": 1, "false_alarms": 1, "waiting": 1}


def test_passes_outside_the_hours_judged_now_are_kept_and_counted_apart(
    client: TestClient, store: Store
) -> None:
    seed_week(store)  # at 09:00 and 09:10, 08:05, 17:30 and 00:00 on five different days

    def outside() -> int:
        body = client.get("/api/stats/stops", params={"days": 7}).json()
        assert body["totals"] == {"stopped": 2, "rolling": 1, "did_not_stop": 1, "indeterminate": 1}
        return body["outside_hours"]

    assert outside() == 0  # judged at all hours: none is turned away
    store.set_stop_hours(StopHours(clock=True, ranges=(TimeRange(9 * 60, 17 * 60),)))
    assert outside() == 3  # the early one, the one after five and the one at midnight
    store.set_stop_hours(StopHours(daylight=True, latitude=40.7, longitude=-74.0))
    assert outside() == 1  # the sun is up from before 07:00 to after 18:00 in New York in October
    store.set_stop_hours(StopHours())
    assert outside() == 0


def test_a_false_alarm_outside_the_hours_is_in_no_count(client: TestClient, store: Store) -> None:
    seed_week(store)
    store.set_stop_hours(StopHours(clock=True, ranges=(TimeRange(9 * 60, 17 * 60),)))
    (early,) = store.stops_between(ms("2026-10-08 00:00"), ms("2026-10-09 00:00"))
    store.review_stop(early.id, "false_positive")
    body = client.get("/api/stats/stops", params={"days": 7}).json()
    assert (body["outside_hours"], body["review"]["false_alarms"]) == (2, 1)


def test_speeds_in_words_follow_the_units_the_owner_chose(client: TestClient, store: Store) -> None:
    store.set_setting("units", "kmh")
    assert client.get("/api/stats/stops").json()["units"] == "kmh"
    store.set_setting("units", "furlongs")
    assert client.get("/api/stats/stops").json()["units"] == "mph"


def test_all_time_runs_from_the_day_of_the_first_pass_and_has_no_week_before(
    client: TestClient, store: Store
) -> None:
    store.record_stop(stop("stopped"), ms("2026-10-06 23:59"))
    store.record_stop(stop("rolling"), ms("2026-10-09 09:00"))
    body = client.get("/api/stats/stops", params={"days": 0}).json()
    assert (body["days"], body["all_time"], body["previous"]) == (4, True, None)
    assert body["by_day"][0]["date"] == "2026-10-06"
    assert body["totals"]["stopped"] == 1 and body["totals"]["rolling"] == 1


def test_all_time_with_no_passes_is_today_alone(client: TestClient) -> None:
    body = client.get("/api/stats/stops", params={"days": 0}).json()
    assert (body["days"], body["all_time"]) == (1, True)


def test_with_nothing_recorded_everything_is_zero_and_the_shape_is_whole(
    client: TestClient,
) -> None:
    body = client.get("/api/stats/stops").json()
    assert body["totals"] == {"stopped": 0, "rolling": 0, "did_not_stop": 0, "indeterminate": 0}
    assert (len(body["by_hour"]), len(body["by_day"]), len(body["by_weekday"])) == (24, 7, 7)
    assert body["by_kind"] == [] and body["by_sign"] == []
    assert body["timing"] == {
        "stopped_hold_s": None,
        "rolling_min_speed": None,
        "did_not_stop_min_speed": None,
        "rolling_min_speed_ms": None,
        "did_not_stop_min_speed_ms": None,
    }


def test_the_days_follow_the_zone_that_is_set(client: TestClient, store: Store) -> None:
    store.record_stop(stop("stopped"), ms("2026-10-08 22:00"))  # 02:00 on the 9th in UTC

    def stopped_today() -> int:
        return client.get("/api/stats/stops", params={"days": 1}).json()["totals"]["stopped"]

    assert stopped_today() == 0  # in New York it was yesterday evening
    store.set_setting("timezone", "UTC")
    assert stopped_today() == 1  # in UTC it is on the same date as now
    store.set_setting("timezone", "Asia/Tokyo")  # 11:00 on the 9th; it is the 10th by now
    assert stopped_today() == 0


# --- traffic -------------------------------------------------------------------------------------

LINE = {"id": "north", "name": "Northbound", "a": [0.2, 0.5], "b": [0.8, 0.5]}


def count(
    store: Store, when: str, line: str = "north", direction: int = 1, label: str = "car"
) -> None:
    store.record_count(CountEvent(0, 1, 2, label, line, direction), ms(when))


def seed_traffic(store: Store) -> None:
    store.set_profile({"lines": [LINE]})
    for minute in (0, 1, 2):
        count(store, f"2026-10-09 18:0{minute}")
    count(store, "2026-10-09 18:30", direction=-1, label="truck")
    count(store, "2026-10-08 08:00")
    count(store, "2026-10-08 08:01", line="deleted", label="bus")
    count(store, "2026-09-01 08:00")  # long before


def test_traffic_is_summed_for_the_days_asked_for(client: TestClient, store: Store) -> None:
    seed_traffic(store)
    body = client.get("/api/stats/traffic", params={"days": 7}).json()
    assert (body["total"], body["removed"], body["days"]) == (6, 1, 7)
    assert body["by_class"] == {"car": 4, "truck": 1, "bus": 1}
    assert [
        (line["id"], line["name"], line["forward"], line["reverse"]) for line in body["lines"]
    ] == [("north", "Northbound", 4, 1)]
    assert body["by_hour"][18]["total"] == 4 and body["by_hour"][8]["total"] == 2
    assert [(d["date"], d["total"]) for d in body["by_day"][-2:]] == [
        ("2026-10-08", 2),
        ("2026-10-09", 4),
    ]
    assert [w["total"] for w in body["by_weekday"]] == [0, 0, 0, 2, 4, 0, 0]  # Thursday, Friday


def test_all_time_traffic_starts_on_the_day_of_the_first_count(
    client: TestClient, store: Store
) -> None:
    seed_traffic(store)
    body = client.get("/api/stats/traffic", params={"days": 0}).json()
    assert body["all_time"] is True and body["days"] == 39 and body["total"] == 7
    assert body["by_day"][0]["date"] == "2026-09-01"


def test_with_nothing_counted_traffic_is_zero_with_every_hour_and_day(client: TestClient) -> None:
    body = client.get("/api/stats/traffic", params={"days": 3}).json()
    assert (body["total"], body["by_class"], body["lines"]) == (0, {}, [])
    assert (len(body["by_hour"]), len(body["by_day"]), len(body["by_weekday"])) == (24, 3, 7)


def quick(label: str, max_speed: float) -> StopEvent:
    return StopEvent(
        0, 7, 2, label, "elm", "did_not_stop",  # type: ignore[arg-type]
        hold_s=0.0, min_speed=1.0, observed_s=3.0, max_speed=max_speed,
    )  # fmt: skip


def test_each_days_fastest_estimate_and_the_periods_come_with_the_stats(
    client: TestClient, store: Store
) -> None:
    store.record_stop(quick("car", 4.0), ms("2026-10-09 17:42"))
    store.record_stop(quick("truck", 3.0), ms("2026-10-08 12:00"))
    body = client.get("/api/stats/stops", params={"days": 3}).json()
    by_date = {d["date"]: d["fastest"] for d in body["by_day"]}
    assert by_date == {
        "2026-10-07": None,
        "2026-10-08": {
            "speed_ms": 10.5,
            "label": "truck",
            "at": ms("2026-10-08 12:00"),
            "calibrated": False,
        },
        "2026-10-09": {
            "speed_ms": 10.0,
            "label": "car",
            "at": ms("2026-10-09 17:42"),
            "calibrated": False,
        },
    }
    assert body["fastest"] == {
        "speed_ms": 10.5,
        "label": "truck",
        "at": ms("2026-10-08 12:00"),
        "calibrated": False,
    }


def test_passes_from_before_the_fastest_speed_was_kept_give_no_speed_and_no_error(
    client: TestClient, store: Store
) -> None:
    seed_week(store)  # these were made without one
    body = client.get("/api/stats/stops", params={"days": 7}).json()
    assert body["fastest"] is None
    assert all(day["fastest"] is None for day in body["by_day"])


def test_the_estimate_is_in_metres_a_second_whatever_units_the_owner_chose(
    client: TestClient, store: Store
) -> None:
    store.record_stop(quick("car", 4.0), ms("2026-10-09 10:00"))
    store.set_setting("units", "kmh")
    body = client.get("/api/stats/stops", params={"days": 1}).json()
    assert body["units"] == "kmh" and body["fastest"]["speed_ms"] == 10.0


# --- the speeds of the vehicles counted (plan.md M6) --------------------------------------------


def counted(
    line: str = "main",
    speed_ms: float | None = None,
    label: str = "car",
    calibration: str | None = None,
) -> CountEvent:
    """A count, with a speed, measured under the calibration the profile holds (unless told)."""
    return CountEvent(
        0, 1, 2, label, line, 1, speed_ms=speed_ms, calibration=calibration or name_of()
    )


@pytest.fixture
def calibrated(store: Store) -> str:
    """The profile holds a calibration; the speeds of the tests are measured under its name."""
    store.set_profile({"calibration": CALIBRATION})
    return name_of()


def test_the_speeds_need_a_login(app: FastAPI) -> None:
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/stats/speeds").status_code == 401


def test_with_no_counts_the_speed_figures_are_empty_and_the_shape_is_whole(
    client: TestClient,
) -> None:
    body = client.get("/api/stats/speeds").json()
    assert body["figures"] is None and body["counted"] == 0 and body["bins"] == []
    assert (len(body["by_hour"]), len(body["by_day"])) == (24, 7)
    assert body["units"] == "mph" and body["calibration"] is None and body["excluded"] == 0


def test_vehicles_counted_with_a_speed_are_summed_and_those_without_are_counted_apart(
    client: TestClient, store: Store, calibrated: str
) -> None:
    store.record_count(counted(speed_ms=11.176), ms("2026-10-09 09:00"))  # 25 mph
    store.record_count(counted(speed_ms=13.411), ms("2026-10-09 09:05"))  # 30 mph
    store.record_count(counted(), ms("2026-10-09 09:10"))  # counted, no speed
    body = client.get("/api/stats/speeds", params={"days": 7}).json()
    assert body["counted"] == 3
    assert body["figures"]["count"] == 2 and body["figures"]["mean"] == pytest.approx(27.5, abs=0.1)
    assert body["calibration"] == calibrated and body["excluded"] == 0
    (line,) = [row for row in body["by_line"] if row["line"] == "main"]
    assert line["counted"] == 3 and line["figures"]["count"] == 2


def test_the_units_are_the_ones_that_are_set(
    client: TestClient, store: Store, calibrated: str
) -> None:
    store.record_count(counted(speed_ms=10.0), ms("2026-10-09 09:00"))
    assert client.get("/api/stats/speeds").json()["figures"]["median"] == pytest.approx(
        22.4, abs=0.1
    )
    store.set_setting("units", "kmh")
    body = client.get("/api/stats/speeds").json()
    assert body["units"] == "kmh" and body["figures"]["median"] == 36.0


def test_one_counting_line_can_be_asked_for(
    client: TestClient, store: Store, calibrated: str
) -> None:
    store.record_count(counted("main", 10.0), ms("2026-10-09 09:00"))
    store.record_count(counted("side", 20.0), ms("2026-10-09 09:05"))
    body = client.get("/api/stats/speeds", params={"line": "side"}).json()
    assert body["line"] == "side" and body["counted"] == 1
    assert body["figures"]["count"] == 1 and body["figures"]["median"] == pytest.approx(
        44.7, abs=0.1
    )
    assert [row["line"] for row in body["by_line"]] == ["side"]


def test_the_period_is_in_local_days_and_all_time_starts_at_the_first_count(
    client: TestClient, store: Store, calibrated: str
) -> None:
    store.record_count(counted(speed_ms=10.0), ms("2026-10-09 09:00"))
    store.record_count(counted(speed_ms=10.0), ms("2026-10-01 09:00"))  # eight days ago
    assert client.get("/api/stats/speeds", params={"days": 7}).json()["figures"]["count"] == 1
    everything = client.get("/api/stats/speeds", params={"days": 0}).json()
    assert everything["figures"]["count"] == 2 and everything["all_time"] is True
    assert everything["days"] == 9


@pytest.mark.parametrize("params", [{"days": -1}, {"days": 400}, {"line": "x" * 70}])
def test_a_request_that_makes_no_sense_is_refused(
    client: TestClient, params: dict[str, int | str]
) -> None:
    assert client.get("/api/stats/speeds", params=params).status_code == 422


def test_speeds_measured_under_an_earlier_calibration_are_left_out_and_counted(
    client: TestClient, store: Store, calibrated: str
) -> None:
    """The rectangle was wrong and has been corrected: the figures are those of the right one."""
    old = name_of(CALIBRATION)
    store.set_profile({"calibration": OTHER})
    store.record_count(counted(speed_ms=10.0, calibration=old), ms("2026-10-09 09:00"))
    store.record_count(counted(speed_ms=30.0, calibration=old), ms("2026-10-09 09:05"))
    store.record_count(counted(speed_ms=20.0, calibration=name_of(OTHER)), ms("2026-10-09 09:10"))
    body = client.get("/api/stats/speeds").json()
    assert body["calibration"] == name_of(OTHER) and body["excluded"] == 2
    assert body["figures"]["count"] == 1 and body["figures"]["median"] == pytest.approx(
        44.7, abs=0.1
    )
    assert body["counted"] == 3  # the vehicles were counted all the same


def test_with_no_calibration_in_force_no_speed_is_shown(client: TestClient, store: Store) -> None:
    store.record_count(counted(speed_ms=10.0, calibration="ab12cd34"), ms("2026-10-09 09:00"))
    body = client.get("/api/stats/speeds").json()
    assert body["calibration"] is None and body["figures"] is None and body["excluded"] == 1
    assert body["counted"] == 1
