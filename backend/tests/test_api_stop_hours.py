"""The endpoints behind the Stop-sign hours card."""

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from trafficpi.api.app import create_app
from trafficpi.api.auth import hash_password
from trafficpi.api.settings import ApiSettings
from trafficpi.api.vision_client import VisionClient
from trafficpi.core import stop_hours as sh
from trafficpi.core.clock import WallClock
from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.store import Store

PASSWORD = "correct horse battery"
NEW_YORK = ZoneInfo("America/New_York")
MINUTE = 60_000
NYC = {"latitude": 40.7128, "longitude": -74.006}


def at(year: int, month: int, day: int, hour: int, minute: int = 0) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=NEW_YORK).timestamp() * 1000)


class StubClock(WallClock):
    def __init__(self, now: int) -> None:
        super().__init__()
        self.now = now

    def now_ms(self) -> int:
        return self.now


def config(**changes: Any) -> dict[str, Any]:
    return {
        "daylight": False,
        "latitude": None,
        "longitude": None,
        "before_sunrise_min": 30,
        "after_sunset_min": 30,
        "clock": False,
        "ranges": [{"start": "07:00", "end": "19:00"}],
        "days": [0, 1, 2, 3, 4, 5, 6],
        **changes,
    }


@pytest.fixture(scope="module")
def password_hash() -> str:
    return hash_password(PASSWORD)


@pytest.fixture
def store(password_hash: str) -> Store:
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    s = Store(engine)
    s.ensure_camera()
    s.create_user("admin", password_hash)
    s.set_setting("timezone", "America/New_York")
    return s


@pytest.fixture
def reloads() -> list[str]:
    return []


def make_client(
    store: Store, tmp_path: Path, reloads: list[str], *, vision_status: int = 200
) -> TestClient:
    def vision(request: httpx.Request) -> httpx.Response:
        reloads.append(request.url.path)
        return httpx.Response(vision_status, json={"ok": vision_status == 200})

    app = create_app(
        ApiSettings(data_dir=tmp_path, dashboard_dir=None),
        store=store,
        vision=VisionClient("http://vision", transport=httpx.MockTransport(vision)),
    )
    app.state.ctx.clock = StubClock(at(2026, 10, 9, 20, 41))  # night, in New York
    return TestClient(app)


@pytest.fixture
def client(store: Store, tmp_path: Path, reloads: list[str]) -> Iterator[TestClient]:
    with make_client(store, tmp_path, reloads) as c:
        assert (
            c.post("/api/auth/login", json={"username": "admin", "password": PASSWORD}).status_code
            == 204
        )
        yield c


def set_clock(client: TestClient, now: int) -> None:
    client.app.state.ctx.clock.now = now  # type: ignore[attr-defined]


# --- reading ----------------------------------------------------------------------------------


def test_until_something_is_set_nothing_is_limited_and_every_pass_is_judged(
    client: TestClient,
) -> None:
    state = client.get("/api/stops/hours").json()
    assert state["config"] == config()
    status = state["status"]
    assert (status["limited"], status["judging"], status["next_change"]) == (False, True, None)
    assert status["description"] == "at all hours"
    assert status["day"] is None  # no place yet, so no sun
    assert status["timezone"] == "America/New_York"


def test_reading_does_not_bother_the_vision_service(client: TestClient, reloads: list[str]) -> None:
    client.get("/api/stops/hours")
    assert reloads == []


# --- saving -----------------------------------------------------------------------------------


def test_the_daylight_limit_is_saved_and_the_vision_service_is_told_to_reload(
    client: TestClient, store: Store, reloads: list[str]
) -> None:
    sent = config(daylight=True, **NYC)
    saved = client.put("/api/stops/hours", json=sent)
    assert saved.status_code == 200
    assert saved.json()["config"] == sent
    assert client.get("/api/stops/hours").json()["config"] == sent
    assert store.get_stop_hours().daylight is True  # what the vision service reads
    assert store.get_setting(sh.DAYLIGHT_KEY) is True
    assert reloads == ["/reload"]  # so the change applies at once, with no restart


def test_a_save_says_whether_the_vision_service_was_told(
    client: TestClient, reloads: list[str]
) -> None:
    saved = client.put("/api/stops/hours", json=config(clock=True)).json()
    assert saved["vision_notified"] is True
    assert reloads == ["/reload"]


def test_a_save_that_the_vision_service_could_not_be_told_of_is_still_saved_and_says_so(
    store: Store, tmp_path: Path
) -> None:
    # the service is down or busy: it reads the hours when it next starts, and the page must not
    # claim that it already uses them
    refused: list[str] = []
    with make_client(store, tmp_path, refused, vision_status=503) as c:
        c.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
        saved = c.put("/api/stops/hours", json=config(clock=True))
    assert saved.status_code == 200
    assert saved.json()["vision_notified"] is False
    assert saved.json()["config"]["clock"] is True
    assert store.get_stop_hours().clock is True  # and what is stored is what the service will read


def test_reading_and_checking_say_nothing_about_the_vision_service(client: TestClient) -> None:
    assert "vision_notified" not in client.get("/api/stops/hours").json()
    assert "vision_notified" not in client.post("/api/stops/hours/check", json=config()).json()


def test_set_hours_with_several_ranges_one_through_midnight_and_some_days_are_kept(
    client: TestClient, store: Store
) -> None:
    sent = config(
        clock=True,
        ranges=[
            {"start": "07:30", "end": "09:00"},
            {"start": "14:00", "end": "16:00"},
            {"start": "22:00", "end": "06:00"},
        ],
        days=[0, 1, 2, 3, 4],
    )
    assert client.put("/api/stops/hours", json=sent).json()["config"] == sent
    hours = store.get_stop_hours()
    assert hours.clock and len(hours.ranges) == 3 and hours.ranges[2].through_midnight
    assert hours.days == frozenset(range(5))


def test_days_come_back_in_order_and_once(client: TestClient) -> None:
    saved = client.put("/api/stops/hours", json=config(clock=True, days=[4, 0, 4, 2])).json()
    assert saved["config"]["days"] == [0, 2, 4]


def test_a_place_is_kept_while_the_daylight_limit_is_off(client: TestClient) -> None:
    saved = client.put("/api/stops/hours", json=config(daylight=False, **NYC)).json()
    assert saved["config"]["daylight"] is False
    assert (saved["config"]["latitude"], saved["config"]["longitude"]) == (40.7128, -74.006)
    assert saved["status"]["limited"] is False


def test_a_whole_number_will_do_for_a_place(client: TestClient) -> None:
    saved = client.put("/api/stops/hours", json=config(latitude=41, longitude=-74)).json()
    assert (saved["config"]["latitude"], saved["config"]["longitude"]) == (41.0, -74.0)


def test_a_limit_can_be_taken_off_again(client: TestClient, store: Store) -> None:
    client.put("/api/stops/hours", json=config(daylight=True, clock=True, **NYC))
    client.put("/api/stops/hours", json=config(daylight=False, clock=False, **NYC))
    assert not store.get_stop_hours().limited


# --- what it says -----------------------------------------------------------------------------


def test_at_night_it_says_judging_is_paused_and_when_it_next_opens(client: TestClient) -> None:
    status = client.put("/api/stops/hours", json=config(daylight=True, **NYC)).json()["status"]
    assert status["limited"] is True and status["judging"] is False
    assert status["description"] == "from 30 minutes before sunrise to 30 minutes after sunset"
    assert status["now"] == at(2026, 10, 9, 20, 41)
    assert abs(status["next_change"] - at(2026, 10, 10, 6, 32)) <= 3 * MINUTE


def test_by_day_it_says_judging_is_on_and_when_it_closes(client: TestClient) -> None:
    client.put("/api/stops/hours", json=config(daylight=True, **NYC))
    set_clock(client, at(2026, 10, 9, 12, 0))
    status = client.get("/api/stops/hours").json()["status"]
    assert status["judging"] is True
    assert abs(status["next_change"] - at(2026, 10, 9, 18, 55)) <= 2 * MINUTE


def test_the_suns_day_is_given_for_the_page(client: TestClient) -> None:
    day = client.put("/api/stops/hours", json=config(daylight=True, **NYC)).json()["status"]["day"]
    assert day["date"] == "2026-10-09"
    assert abs(day["sunrise"] - at(2026, 10, 9, 7, 1)) <= MINUTE
    assert abs(day["sunset"] - at(2026, 10, 9, 18, 25)) <= MINUTE
    assert day["start"] == day["sunrise"] - 30 * MINUTE
    assert day["end"] == day["sunset"] + 30 * MINUTE
    assert day["polar"] is None
    assert abs(day["noon_offset_min"] - 43) <= 1


def test_the_suns_day_is_given_while_the_limit_is_off_so_the_page_can_show_it_first(
    client: TestClient,
) -> None:
    status = client.put("/api/stops/hours", json=config(daylight=False, **NYC)).json()["status"]
    assert status["day"] is not None and status["day"]["sunrise"] is not None
    assert (status["limited"], status["judging"]) == (False, True)  # but nothing is limited yet


def test_the_set_hours_decide_by_the_cameras_clock_and_its_days(client: TestClient) -> None:
    client.put("/api/stops/hours", json=config(clock=True, days=[0, 1, 2, 3, 4]))
    set_clock(client, at(2026, 10, 9, 12, 0))  # a Friday
    assert client.get("/api/stops/hours").json()["status"]["judging"] is True
    set_clock(client, at(2026, 10, 10, 12, 0))  # a Saturday
    status = client.get("/api/stops/hours").json()["status"]
    assert status["judging"] is False
    assert status["next_change"] == at(2026, 10, 12, 7, 0)  # Monday morning
    assert status["description"] == "from 07:00 to 19:00 on weekdays"


def test_a_longitude_with_the_wrong_sign_puts_the_sun_hours_from_noon(client: TestClient) -> None:
    wrong = client.post(
        "/api/stops/hours/check", json=config(daylight=True, latitude=40.7128, longitude=74.006)
    ).json()
    assert abs(wrong["day"]["noon_offset_min"]) > 8 * 60  # the page warns about this
    right = client.post("/api/stops/hours/check", json=config(daylight=True, **NYC)).json()
    assert abs(right["day"]["noon_offset_min"]) < 60


def test_the_midnight_sun_is_told_as_such(client: TestClient, store: Store) -> None:
    oslo = ZoneInfo("Europe/Oslo")
    store.set_setting("timezone", "Europe/Oslo")
    set_clock(client, int(datetime(2026, 6, 21, 12, tzinfo=oslo).timestamp() * 1000))
    status = client.post(
        "/api/stops/hours/check",
        json=config(daylight=True, latitude=69.6492, longitude=18.9553),
    ).json()
    assert status["day"]["polar"] == "day" and status["day"]["sunrise"] is None
    assert status["judging"] is True and status["next_change"] is None


# --- the preview ------------------------------------------------------------------------------


def test_a_check_answers_for_a_draft_without_saving_it_or_waking_the_vision_service(
    client: TestClient, store: Store, reloads: list[str]
) -> None:
    status = client.post("/api/stops/hours/check", json=config(daylight=True, **NYC))
    assert status.status_code == 200
    assert status.json()["judging"] is False and status.json()["limited"] is True
    assert store.get_stop_hours().limited is False  # nothing was saved
    assert reloads == []


# --- what is refused --------------------------------------------------------------------------


def test_the_daylight_limit_needs_a_place_and_nothing_is_saved_without_one(
    client: TestClient, store: Store, reloads: list[str]
) -> None:
    refused = client.put("/api/stops/hours", json=config(daylight=True))
    assert refused.status_code == 422
    assert "latitude and longitude" in refused.json()["detail"]
    assert store.get_stop_hours() == sh.StopHours()
    assert store.get_setting(sh.DAYLIGHT_KEY) is None  # not even a part of it was written
    assert reloads == []


@pytest.mark.parametrize(
    "changes",
    [
        {"latitude": 90.5, "longitude": 0},
        {"latitude": -91, "longitude": 0},
        {"latitude": 0, "longitude": 180.5},
        {"latitude": 0, "longitude": -181},
        {"latitude": 40.0},  # without a longitude
        {"longitude": -74.0},  # without a latitude
        {"latitude": "40.7", "longitude": "-74.0"},
        {
            "latitude": "40.7",
            "longitude": -74.0,
        },  # each one on its own, so that neither hides the other
        {"latitude": 40.7, "longitude": "-74.0"},
        {"latitude": True, "longitude": False},
        {"latitude": True, "longitude": -74.0},
        {"latitude": 40.7, "longitude": True},
        {"before_sunrise_min": 181},
        {"before_sunrise_min": -121},
        {"after_sunset_min": 181},
        {"after_sunset_min": -121},
        {"before_sunrise_min": 30.5},
        {"before_sunrise_min": "30"},
        {"before_sunrise_min": True},
        {"ranges": []},
        {"ranges": [{"start": "07:00", "end": "19:00"}] * 7},
        {"ranges": [{"start": "7:00", "end": "19:00"}]},
        {"ranges": [{"start": "07:00", "end": "24:00"}]},
        {"ranges": [{"start": "07:00", "end": "07:00"}]},
        {"ranges": [{"start": "07:00"}]},
        {"ranges": [{"start": 700, "end": 1900}]},
        {"ranges": "07:00-19:00"},
        {"days": []},
        {"days": [7]},
        {"days": [-1]},
        {"days": ["0"]},
        {"days": [True]},
        {"days": [0.5]},
        {"daylight": "yes"},
        {"daylight": 1},
        {"clock": "true"},
        {"clock": None},
    ],
)
def test_things_that_make_no_sense_are_refused_and_nothing_is_saved(
    client: TestClient, store: Store, reloads: list[str], changes: dict[str, Any]
) -> None:
    for method, path in (("put", "/api/stops/hours"), ("post", "/api/stops/hours/check")):
        response = getattr(client, method)(path, json=config(**changes))
        assert response.status_code == 422, (method, changes, response.text)
    assert store.get_stop_hours() == sh.StopHours()
    assert reloads == []


@pytest.mark.parametrize("missing", list(config()))
def test_a_body_that_leaves_something_out_is_refused_not_read_as_a_default(
    client: TestClient, store: Store, missing: str
) -> None:
    """A PUT replaces everything: a client that does not know a member must not reset it."""
    incomplete = config()
    del incomplete[missing]
    assert client.put("/api/stops/hours", json=incomplete).status_code == 422
    assert store.get_setting(sh.DAYLIGHT_KEY) is None


def test_the_message_for_a_bad_range_is_a_sentence_the_page_can_show(client: TestClient) -> None:
    refused = client.put(
        "/api/stops/hours", json=config(clock=True, ranges=[{"start": "07:00", "end": "07:00"}])
    )
    assert refused.status_code == 422
    assert isinstance(refused.json()["detail"], str) and refused.json()["detail"]


# --- who may ----------------------------------------------------------------------------------


def test_nothing_here_is_for_anyone_who_is_not_logged_in(
    store: Store, tmp_path: Path, reloads: list[str]
) -> None:
    with make_client(store, tmp_path, reloads) as anonymous:
        assert anonymous.get("/api/stops/hours").status_code == 401
        assert anonymous.put("/api/stops/hours", json=config()).status_code == 401
        assert anonymous.post("/api/stops/hours/check", json=config()).status_code == 401
    assert store.get_stop_hours() == sh.StopHours()
    assert reloads == []


def test_a_change_asked_for_by_another_site_is_refused(
    client: TestClient, store: Store, reloads: list[str]
) -> None:
    refused = client.put(
        "/api/stops/hours",
        json=config(clock=True),
        headers={"Origin": "https://evil.example"},
    )
    assert refused.status_code == 403
    assert store.get_stop_hours() == sh.StopHours()
    assert reloads == []


def test_a_huge_body_is_turned_away_before_it_is_read(client: TestClient) -> None:
    huge = config(ranges=[{"start": "07:00", "end": "19:00"}] * 50_000)
    assert client.put("/api/stops/hours", json=huge).status_code == 413


# --- damage -----------------------------------------------------------------------------------


def test_a_damaged_stored_value_reads_as_no_limit_and_can_be_saved_over(
    client: TestClient, store: Store
) -> None:
    store.set_setting(sh.DAYLIGHT_KEY, "yes")
    store.set_setting(sh.RANGES_KEY, "garbage")
    state = client.get("/api/stops/hours").json()
    assert state["config"] == config()
    assert state["status"]["limited"] is False
    assert client.put("/api/stops/hours", json=config(clock=True)).status_code == 200
    assert store.get_stop_hours().clock is True
