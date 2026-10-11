"""The endpoint behind the calibration editor: what a calibration means."""

import math
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.camera_sim import PinholeCamera
from trafficpi.api.app import create_app
from trafficpi.api.auth import hash_password
from trafficpi.api.settings import ApiSettings
from trafficpi.api.vision_client import VisionClient
from trafficpi.core.ground import MAX_TRUSTED_ERROR
from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.store import Store

PASSWORD = "correct horse battery"
CAMERA = PinholeCamera()
ROAD = (-4.0, 4.0, 8.0, 30.0)  # x, y of the first corner, width and length in metres


def picture(x: float, y: float) -> list[float]:
    seen = CAMERA.project(x, y)
    assert seen is not None
    return [seen[0], seen[1]]


def calibration(**changes: Any) -> dict[str, Any]:
    x, y, width, length = ROAD
    return {
        "corners": [
            picture(x, y),
            picture(x + width, y),
            picture(x + width, y + length),
            picture(x, y + length),
        ],
        "width_m": width,
        "length_m": length,
        **changes,
    }


@pytest.fixture(scope="module")
def password_hash() -> str:
    return hash_password(PASSWORD)


@pytest.fixture
def client(password_hash: str, tmp_path: Path) -> Iterator[TestClient]:
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    store = Store(engine)
    store.ensure_camera()
    store.create_user("admin", password_hash)
    app = create_app(
        ApiSettings(data_dir=tmp_path, dashboard_dir=None),
        store=store,
        vision=VisionClient(
            "http://vision", transport=httpx.MockTransport(lambda r: httpx.Response(200))
        ),
    )
    with TestClient(app) as c:
        c.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
        yield c


def ask(client: TestClient, **body: Any) -> dict[str, Any]:
    response = client.post("/api/calibration/report", json={"calibration": calibration(), **body})
    assert response.status_code == 200, response.text
    return response.json()


# --- who may ask ------------------------------------------------------------------------------


def test_it_needs_the_login(client: TestClient) -> None:
    client.post("/api/auth/logout")
    response = client.post("/api/calibration/report", json={"calibration": calibration()})
    assert response.status_code == 401


# --- a good calibration -------------------------------------------------------------------------


def test_the_report_of_a_calibration_without_checks_or_probes_has_the_grid(
    client: TestClient,
) -> None:
    answer = ask(client)
    assert answer["ok"] is True and answer["problem"] is None
    report = answer["report"]
    assert len(report["id"]) == 8
    assert report["limit_pct"] == MAX_TRUSTED_ERROR * 100
    assert report["checks"] == [] and report["probes"] == []
    grid = report["grid"]
    assert (grid["columns"], grid["rows"]) == (32, 18)
    assert len(grid["along_pct"]) == 18 and all(len(row) == 32 for row in grid["along_pct"])
    assert len(grid["across_pct"]) == 18
    flat = [v for row in grid["along_pct"] for v in row if v is not None]
    assert flat and min(flat) < report["limit_pct"] < max(flat)  # believed here, not there
    assert 0 < report["centre_error_pct"] < report["limit_pct"]  # the middle of the rectangle


def test_a_known_distance_is_measured_by_the_map_and_compared_with_what_was_typed(
    client: TestClient,
) -> None:
    check = {
        "id": "pole",
        "name": "Driveway to the pole",
        "a": picture(-1.0, 8.0),
        "b": picture(2.0, 29.0),
        "length_m": math.hypot(3.0, 21.0),
    }
    report = ask(client, calibration=calibration(checks=[check]))["report"]
    (result,) = report["checks"]
    assert result["id"] == "pole" and result["entered_m"] == pytest.approx(21.21, abs=0.01)
    assert result["measured_m"] == pytest.approx(21.21, abs=0.01)  # an exact calibration
    assert result["error_pct"] == 0.0
    assert 0 < result["expected_pct"] < 10
    assert result["agrees"] is True


def test_a_check_that_is_far_out_says_it_disagrees_and_by_how_much(client: TestClient) -> None:
    check = {
        "id": "pole",
        "name": "",
        "a": picture(-1.0, 8.0),
        "b": picture(2.0, 29.0),
        "length_m": 15.0,  # typed wrongly: it is 21.2 m
    }
    (result,) = ask(client, calibration=calibration(checks=[check]))["report"]["checks"]
    assert result["measured_m"] == pytest.approx(21.21, abs=0.01)
    assert result["error_pct"] == pytest.approx(41.4, abs=0.1)
    assert result["agrees"] is False


def test_a_small_error_in_a_check_is_within_what_is_expected(client: TestClient) -> None:
    check = {"id": "p", "a": picture(-1.0, 8.0), "b": picture(2.0, 29.0), "length_m": 21.2 * 1.03}
    (result,) = ask(client, calibration=calibration(checks=[check]))["report"]["checks"]
    assert result["error_pct"] == pytest.approx(-2.9, abs=0.2)
    assert result["agrees"] is True


# --- places --------------------------------------------------------------------------------------


def test_a_place_is_told_how_many_metres_a_pixel_is_and_whether_a_speed_is_believed(
    client: TestClient,
) -> None:
    probes = [
        {"id": "stop-line", "point": picture(0.0, 20.0)},  # in the middle of the rectangle
        {"id": "far", "point": picture(0.0, 120.0)},  # a long way up the road
    ]
    report = ask(client, probes=probes)["report"]
    inside, far = report["probes"]
    assert inside["id"] == "stop-line" and inside["believed"] is True
    assert 0 < inside["metres_per_pixel_across"] < inside["metres_per_pixel_along"] < 1
    assert inside["error_along_pct"] < report["limit_pct"]
    # so far up the road that the map has no answer, or a poor one
    assert far["believed"] is False
    assert far["error_along_pct"] is None or far["error_along_pct"] > report["limit_pct"]
    assert far["metres_per_pixel_along"] > inside["metres_per_pixel_along"]


def test_a_place_in_the_sky_has_nothing_to_say_and_nothing_is_believed_there(
    client: TestClient,
) -> None:
    flat = PinholeCamera(x=0.0, y=-25.0, yaw_deg=0.0, pitch_deg=18.0, height=6.0)
    corners = [flat.project(*p) for p in ((-4.0, 4.0), (4.0, 4.0), (4.0, 34.0), (-4.0, 34.0))]
    sky = {"id": "sky", "point": [0.5, 0.01]}
    body = {"corners": [list(c) for c in corners if c], "width_m": 8.0, "length_m": 30.0}
    (result,) = ask(client, calibration=body, probes=[sky])["report"]["probes"]
    assert result["believed"] is False
    assert result["metres_per_pixel_along"] is None and result["error_along_pct"] is None


def test_the_grid_has_no_answer_where_the_road_does_not_reach(client: TestClient) -> None:
    flat = PinholeCamera(x=0.0, y=-25.0, yaw_deg=0.0, pitch_deg=18.0, height=6.0)
    corners = [flat.project(*p) for p in ((-4.0, 4.0), (4.0, 4.0), (4.0, 34.0), (-4.0, 34.0))]
    body = {"corners": [list(c) for c in corners if c], "width_m": 8.0, "length_m": 30.0}
    grid = ask(client, calibration=body)["report"]["grid"]
    assert all(v is None for v in grid["along_pct"][0])  # the top row is above the horizon
    assert any(v is not None for v in grid["along_pct"][-1])


# --- a calibration that cannot be used ------------------------------------------------------------


def test_a_calibration_that_makes_no_sense_is_answered_with_the_reason_not_an_error(
    client: TestClient,
) -> None:
    good = calibration()
    bow_tie = {
        **good,
        "corners": [good["corners"][0], good["corners"][2], good["corners"][1], good["corners"][3]],
    }
    answer = ask(client, calibration=bow_tie)
    assert answer["ok"] is False and answer["report"] is None
    assert "one after the other" in answer["problem"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"width_m": 0.1}, "width_m"),
        ({"length_m": "30"}, "length_m"),
        ({"corners": [[0.1, 0.1]]}, "corners"),
        ({"colour": "red"}, "not something TrafficPi knows"),
        (
            {"checks": [{"id": "a", "a": [0.5, 0.5], "b": [0.5, 0.5], "length_m": 5.0}]},
            "same place",
        ),
    ],
)
def test_each_way_a_calibration_can_be_wrong_is_told_in_words(
    client: TestClient, change: dict[str, Any], message: str
) -> None:
    answer = ask(client, calibration=calibration(**change))
    assert answer["ok"] is False and message in answer["problem"]


@pytest.mark.parametrize("junk", [None, 5, "x", [], {}])
def test_a_calibration_that_is_not_one_is_answered_politely(client: TestClient, junk: Any) -> None:
    response = client.post("/api/calibration/report", json={"calibration": junk})
    assert response.status_code == 200 and response.json()["ok"] is False


def test_a_probe_outside_the_picture_or_too_many_probes_is_refused(client: TestClient) -> None:
    sent = {"calibration": calibration(), "probes": [{"id": "x", "point": [1.5, 0.5]}]}
    response = client.post("/api/calibration/report", json=sent)
    assert response.status_code == 422 and "inside the picture" in response.json()["detail"]
    many = {
        "calibration": calibration(),
        "probes": [{"id": str(i), "point": [0.5, 0.5]} for i in range(65)],
    }
    assert client.post("/api/calibration/report", json=many).status_code == 422


def test_an_enormous_body_is_refused_before_it_is_read(client: TestClient) -> None:
    body = {"calibration": calibration(), "junk": "x" * 40_000}
    assert client.post("/api/calibration/report", json=body).status_code == 413


def test_the_same_question_gets_the_same_answer(client: TestClient) -> None:
    assert ask(client) == ask(client)
