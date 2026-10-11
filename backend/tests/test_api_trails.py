"""The trails endpoints: a day by the hour, and the paths themselves."""

import logging
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.fakes import finished_trail
from trafficpi.api.app import create_app
from trafficpi.api.auth import hash_password
from trafficpi.api.settings import ApiSettings
from trafficpi.api.vision_client import VisionClient
from trafficpi.core.clock import WallClock
from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.store import Store
from trafficpi.vision.trails import FULL_SCALE

PASSWORD = "correct horse battery"
NEW_YORK = "America/New_York"
HOUR_MS = 3_600_000
DAY_MS = 86_400_000
BASE = 1_760_000_000_000


def at(zone: str, local: str, *, fold: int = 0) -> int:
    """A moment given on the wall clock of a time zone, in ms since the epoch. ``fold`` picks the
    first (0) or the second (1) time a repeated hour happens."""
    moment = datetime.fromisoformat(local).replace(tzinfo=ZoneInfo(zone), fold=fold)
    return round(moment.timestamp() * 1000)


def add(store: Store, *moments: tuple[int, str]) -> None:
    """Store a trail of each kind at each moment (ms since the epoch)."""
    store.add_trails(
        [finished_trail(ms, label=label, track_id=i) for i, (ms, label) in enumerate(moments)]
    )


def set_now(app: FastAPI, ms: int) -> None:
    app.state.ctx.clock = WallClock(wall_ns=lambda: ms * 1_000_000)


@pytest.fixture
def store() -> Store:
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    s = Store(engine)
    s.ensure_camera()
    s.create_user("admin", hash_password(PASSWORD))
    s.set_setting("timezone", "UTC")
    return s


@pytest.fixture
def app(tmp_path: Path, store: Store) -> FastAPI:
    # These endpoints never talk to the vision service; whatever it is asked, it says no.
    vision = VisionClient(
        "http://vision", transport=httpx.MockTransport(lambda request: httpx.Response(404))
    )
    return create_app(
        ApiSettings(data_dir=tmp_path, dashboard_dir=None), store=store, vision=vision
    )


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url="http://testserver") as c:
        login = c.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
        assert login.status_code == 204
        yield c


def test_the_trail_endpoints_need_a_login(app: FastAPI) -> None:
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/trails/day").status_code == 401
        assert anonymous.get("/api/trails", params={"from_ms": 0, "to_ms": 1000}).status_code == 401


# --- a day by the hour ------------------------------------------------------------------------


def test_a_day_is_counted_by_the_local_hour_and_the_kind(client: TestClient, store: Store) -> None:
    store.set_setting("timezone", NEW_YORK)
    add(
        store,
        (at(NEW_YORK, "2025-03-05 00:00:00"), "car"),  # the very start of the day
        (at(NEW_YORK, "2025-03-05 08:10"), "car"),
        (at(NEW_YORK, "2025-03-05 08:50"), "car"),
        (at(NEW_YORK, "2025-03-05 08:55"), "truck"),
        (at(NEW_YORK, "2025-03-05 17:05"), "bus"),
        (at(NEW_YORK, "2025-03-05 23:59:59"), "car"),  # the very end of it
        (at(NEW_YORK, "2025-03-06 00:00:00"), "car"),  # tomorrow
        (at(NEW_YORK, "2025-03-04 23:59:59"), "car"),  # yesterday
    )
    day = client.get("/api/trails/day", params={"day": "2025-03-05"}).json()
    assert (day["day"], day["timezone"], day["total"]) == ("2025-03-05", NEW_YORK, 6)
    assert [h["hour"] for h in day["hours"]] == list(range(24))
    assert {h["hour"]: h["count"] for h in day["hours"] if h["count"]} == {0: 1, 8: 3, 17: 1, 23: 1}
    assert day["hours"][8]["by_kind"] == {"car": 2, "truck": 1}
    assert day["hours"][17]["by_kind"] == {"bus": 1}
    assert day["hours"][5]["by_kind"] == {}
    assert day["hours"][0]["t"] == at(NEW_YORK, "2025-03-05 00:00")
    assert day["hours"][9]["t"] - day["hours"][8]["t"] == HOUR_MS
    # every hour ends where the next begins, and the last ends where the next day does
    assert [h["end"] for h in day["hours"][:-1]] == [h["t"] for h in day["hours"][1:]]
    assert day["hours"][23]["end"] == at(NEW_YORK, "2025-03-06 00:00")


def test_the_first_and_last_days_with_trails_are_dates_on_the_local_calendar(
    client: TestClient, store: Store
) -> None:
    add(
        store,
        (at(NEW_YORK, "2025-03-04 22:30"), "car"),  # 03:30 on the 5th in UTC
        (at(NEW_YORK, "2025-03-06 01:00"), "car"),  # 06:00 on the 6th in UTC
    )
    store.set_setting("timezone", NEW_YORK)
    day = client.get("/api/trails/day", params={"day": "2025-03-05"}).json()
    assert (day["first_day"], day["last_day"]) == ("2025-03-04", "2025-03-06")
    store.set_setting("timezone", "UTC")
    day = client.get("/api/trails/day", params={"day": "2025-03-05"}).json()
    assert (day["first_day"], day["last_day"]) == ("2025-03-05", "2025-03-06")


def test_with_no_trails_a_day_is_twenty_four_empty_hours_and_has_no_first_or_last_day(
    client: TestClient,
) -> None:
    day = client.get("/api/trails/day", params={"day": "2025-03-05"}).json()
    assert (day["total"], day["first_day"], day["last_day"]) == (0, None, None)
    assert len(day["hours"]) == 24
    assert all(h["count"] == 0 and h["by_kind"] == {} for h in day["hours"])


def test_without_a_day_it_is_today_where_the_camera_is(
    app: FastAPI, client: TestClient, store: Store
) -> None:
    set_now(app, at("UTC", "2025-03-06 03:00"))
    store.set_setting("timezone", NEW_YORK)  # still the evening of the 5th there
    assert client.get("/api/trails/day").json()["day"] == "2025-03-05"
    store.set_setting("timezone", "Pacific/Auckland")  # already the afternoon of the 6th
    assert client.get("/api/trails/day").json()["day"] == "2025-03-06"


def test_the_day_the_clocks_go_forward_has_twenty_three_hours(
    client: TestClient, store: Store
) -> None:
    store.set_setting("timezone", NEW_YORK)
    add(
        store,
        (at(NEW_YORK, "2025-03-09 01:50"), "car"),
        (at(NEW_YORK, "2025-03-09 03:10"), "car"),  # the next hour: there is no 2 o'clock
    )
    day = client.get("/api/trails/day", params={"day": "2025-03-09"}).json()
    assert [h["hour"] for h in day["hours"]] == [0, 1, *range(3, 24)]
    assert {h["hour"]: h["count"] for h in day["hours"] if h["count"]} == {1: 1, 3: 1}
    gaps = [b["t"] - a["t"] for a, b in zip(day["hours"], day["hours"][1:], strict=False)]
    assert gaps == [HOUR_MS] * 22


def test_the_day_the_clocks_go_back_has_twenty_five_hours_and_the_repeated_one_twice(
    client: TestClient, store: Store
) -> None:
    store.set_setting("timezone", NEW_YORK)
    add(
        store,
        (at(NEW_YORK, "2025-11-02 01:30", fold=0), "car"),
        (at(NEW_YORK, "2025-11-02 01:30", fold=1), "truck"),
        (at(NEW_YORK, "2025-11-02 01:45", fold=1), "truck"),
    )
    day = client.get("/api/trails/day", params={"day": "2025-11-02"}).json()
    assert [h["hour"] for h in day["hours"]] == [0, 1, 1, *range(2, 24)]
    first, second = day["hours"][1], day["hours"][2]
    assert first["by_kind"] == {"car": 1}
    assert second["by_kind"] == {"truck": 2}
    assert second["t"] - first["t"] == HOUR_MS
    assert day["total"] == 3


def test_a_day_that_changes_by_half_an_hour_ends_its_last_hour_where_the_day_ends(
    client: TestClient, store: Store
) -> None:
    """Lord Howe Island moves its clocks by 30 minutes: the day it does is 24.5 hours long."""
    zone = "Australia/Lord_Howe"
    store.set_setting("timezone", zone)
    day = client.get("/api/trails/day", params={"day": "2025-04-06"}).json()  # the clocks go back
    assert len(day["hours"]) == 25
    last = day["hours"][-1]
    assert last["end"] - last["t"] == HOUR_MS // 2
    assert last["end"] == at(zone, "2025-04-07 00:00")


def test_a_zone_a_half_hour_from_utc_still_gets_whole_local_hours(
    client: TestClient, store: Store
) -> None:
    zone = "Asia/Kolkata"
    store.set_setting("timezone", zone)
    add(store, (at(zone, "2025-03-05 00:10"), "car"), (at(zone, "2025-03-05 23:45"), "car"))
    day = client.get("/api/trails/day", params={"day": "2025-03-05"}).json()
    assert [h["hour"] for h in day["hours"]] == list(range(24))
    assert day["hours"][0]["t"] == at(zone, "2025-03-05 00:00")
    assert (day["hours"][0]["count"], day["hours"][23]["count"]) == (1, 1)


@pytest.mark.parametrize("day", ["2000-01-01", "2100-12-31"])
def test_the_first_and_last_days_that_can_be_asked_about_are_accepted(
    client: TestClient, day: str
) -> None:
    assert client.get("/api/trails/day", params={"day": day}).status_code == 200


@pytest.mark.parametrize(
    "day",
    ["banana", "2025-13-01", "2025-02-30", "0001-01-01", "1999-12-31", "2101-01-01", "9999-12-31"],
)
def test_a_day_that_is_not_a_real_recent_date_is_refused_not_a_crash(
    client: TestClient, day: str
) -> None:
    assert client.get("/api/trails/day", params={"day": day}).status_code == 422


# --- the paths --------------------------------------------------------------------------------


def test_paths_come_back_as_whole_numbers_with_the_scale_to_divide_by(
    client: TestClient, store: Store
) -> None:
    store.add_trails([finished_trail(BASE, label="truck", seconds=1, x0=0.1, y=0.6)])
    response = client.get("/api/trails", params={"from_ms": BASE, "to_ms": BASE + HOUR_MS})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert (body["from_ms"], body["to_ms"]) == (BASE, BASE + HOUR_MS)
    assert (body["total"], body["returned"], body["scale"]) == (1, 1, FULL_SCALE)
    (trail,) = body["trails"]
    assert isinstance(trail["id"], int)
    assert (trail["label"], trail["started_at"], trail["ended_at"]) == ("truck", BASE, BASE + 1000)
    assert len(trail["x"]) == len(trail["y"]) == 6
    assert trail["x"][0] == pytest.approx(0.1 * FULL_SCALE, abs=1)
    assert trail["x"][-1] == pytest.approx(0.7 * FULL_SCALE, abs=1)
    assert trail["y"] == pytest.approx([0.6 * FULL_SCALE] * 6, abs=1)


def test_only_trails_that_began_in_the_range_come_back_oldest_first(
    client: TestClient, store: Store
) -> None:
    add(
        store,
        (BASE + 30_000, "car"),
        (BASE + 10_000, "car"),
        (BASE + HOUR_MS, "car"),  # the end of the range is not in it
        (BASE - 1, "car"),
    )
    body = client.get("/api/trails", params={"from_ms": BASE, "to_ms": BASE + HOUR_MS}).json()
    assert [t["started_at"] - BASE for t in body["trails"]] == [10_000, 30_000]
    assert body["total"] == 2


def test_trails_can_be_asked_for_by_kind(client: TestClient, store: Store) -> None:
    add(
        store,
        (BASE + 1_000, "car"),
        (BASE + 2_000, "truck"),
        (BASE + 3_000, "person"),
        (BASE + 4_000, "bus"),
    )

    def ask(kinds: str | None) -> dict[str, object]:
        params: dict[str, object] = {"from_ms": BASE, "to_ms": BASE + HOUR_MS}
        if kinds is not None:
            params["kinds"] = kinds
        return client.get("/api/trails", params=params).json()  # type: ignore[no-any-return]

    def labels(kinds: str | None) -> list[str]:
        return [t["label"] for t in ask(kinds)["trails"]]  # type: ignore[index]

    assert labels("car") == ["car"]
    assert labels("truck,car") == ["car", "truck"]  # oldest first, in whatever order asked
    assert labels(" car , person ") == ["car", "person"]
    assert labels("car,car") == ["car"]
    assert labels(None) == ["car", "truck", "person", "bus"]
    assert ask("car,truck")["total"] == 2


@pytest.mark.parametrize("kinds", ["dog", "car,dog", "Car"])
def test_a_kind_that_does_not_exist_is_refused_and_the_real_ones_are_listed(
    client: TestClient, kinds: str
) -> None:
    response = client.get("/api/trails", params={"from_ms": 0, "to_ms": 1000, "kinds": kinds})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "unknown kind" in detail and "bicycle, bus, car" in detail


@pytest.mark.parametrize("kinds", ["", " ", ",", "car,,truck"])
def test_an_empty_kind_is_refused_and_the_person_is_told_to_name_one(
    client: TestClient, kinds: str
) -> None:
    response = client.get("/api/trails", params={"from_ms": 0, "to_ms": 1000, "kinds": kinds})
    assert response.status_code == 422
    assert "at least one kind" in response.json()["detail"]


def test_a_big_answer_is_an_even_sample_and_says_how_many_there_were(
    client: TestClient, store: Store
) -> None:
    add(store, *[(BASE + i * 1_000, "car") for i in range(10)])

    def ask(limit: int) -> dict[str, object]:
        params = {"from_ms": BASE, "to_ms": BASE + HOUR_MS, "limit": limit}
        return client.get("/api/trails", params=params).json()  # type: ignore[no-any-return]

    whole = ask(10)
    assert (whole["total"], whole["returned"]) == (10, 10)
    starts = [t["started_at"] for t in whole["trails"]]  # type: ignore[attr-defined]
    sample = ask(4)  # ten do not fit in four: every third one does
    assert (sample["total"], sample["returned"]) == (10, 3)
    assert [t["started_at"] for t in sample["trails"]] == [starts[2], starts[5], starts[8]]  # type: ignore[attr-defined]
    assert ask(9)["returned"] == 5  # every second one
    assert ask(1)["returned"] == 1


def test_the_default_limit_keeps_an_answer_to_a_few_thousand_paths(
    client: TestClient, store: Store
) -> None:
    add(store, *[(BASE + i, "car") for i in range(5_003)])
    body = client.get("/api/trails", params={"from_ms": BASE, "to_ms": BASE + HOUR_MS}).json()
    assert body["total"] == 5_003 and 2_500 <= body["returned"] <= 5_000


@pytest.mark.parametrize(
    "params",
    [
        {"from_ms": 10, "to_ms": 10},
        {"from_ms": 10, "to_ms": 5},
        {"from_ms": 0, "to_ms": 32 * DAY_MS + 1},
        {"from_ms": -1, "to_ms": 5},
        {"from_ms": 0, "to_ms": 10**30},
        {"from_ms": 0, "to_ms": 1000, "limit": 0},
        {"from_ms": 0, "to_ms": 1000, "limit": -5},
        {"from_ms": 0, "to_ms": 1000, "limit": 20_001},
        {"from_ms": "soon", "to_ms": 1000},
        {"to_ms": 1000},
        {"from_ms": 0},
    ],
)
def test_a_request_that_cannot_be_answered_is_refused(
    client: TestClient, params: dict[str, object]
) -> None:
    assert client.get("/api/trails", params=params).status_code == 422  # type: ignore[arg-type]


def test_thirty_two_days_is_the_most_that_can_be_asked_for_at_once(client: TestClient) -> None:
    assert client.get("/api/trails", params={"from_ms": 0, "to_ms": 32 * DAY_MS}).status_code == 200


def test_a_big_answer_is_compressed_for_a_browser_that_accepts_it(
    client: TestClient, store: Store
) -> None:
    add(store, *[(BASE + i * 1_000, "car") for i in range(60)])
    params = {"from_ms": BASE, "to_ms": BASE + HOUR_MS}
    plain = client.get("/api/trails", params=params, headers={"accept-encoding": "identity"})
    packed = client.get("/api/trails", params=params, headers={"accept-encoding": "gzip"})
    assert "content-encoding" not in plain.headers
    assert packed.headers["content-encoding"] == "gzip"
    assert "accept-encoding" in plain.headers["vary"].lower()
    assert "accept-encoding" in packed.headers["vary"].lower()
    assert packed.json() == plain.json()  # the same answer, once unpacked
    assert int(packed.headers["content-length"]) < int(plain.headers["content-length"]) / 2


def test_a_small_answer_is_sent_as_it_is_even_to_a_browser_that_accepts_compression(
    client: TestClient,
) -> None:
    response = client.get(
        "/api/trails", params={"from_ms": 0, "to_ms": 1000}, headers={"accept-encoding": "gzip"}
    )
    assert "content-encoding" not in response.headers
    assert response.json()["trails"] == []


def test_a_damaged_path_is_left_out_and_the_rest_are_still_shown(
    client: TestClient, store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    store.add_trails(
        [
            finished_trail(BASE + 1_000, track_id=1),
            replace(finished_trail(BASE + 2_000, track_id=2), path=b"junk"),
            finished_trail(BASE + 3_000, track_id=3),
        ]
    )
    with caplog.at_level(logging.WARNING, logger="trafficpi.api"):
        response = client.get("/api/trails", params={"from_ms": BASE, "to_ms": BASE + HOUR_MS})
    body = response.json()
    assert [t["started_at"] - BASE for t in body["trails"]] == [1_000, 3_000]
    assert (body["total"], body["returned"]) == (3, 2)
    assert any("could not be read" in record.getMessage() for record in caplog.records)
