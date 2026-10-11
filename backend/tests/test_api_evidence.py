"""The evidence endpoints: the picture and the clip that are kept of a stop."""

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trafficpi.api.app import create_app
from trafficpi.api.auth import hash_password
from trafficpi.api.settings import ApiSettings
from trafficpi.api.vision_client import VisionClient
from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.store import Store
from trafficpi.vision.rules import CountEvent, StopEvent

PASSWORD = "correct horse battery"
BASE = 1_760_000_000_000
RANGE = {"from_ms": BASE - 1_000, "to_ms": BASE + 1_000}
PICTURE = b"\xff\xd8\xff\xe0 not really a picture \xff\xd9"
CLIP = bytes(range(256)) * 4  # 1024 bytes: not a real clip, but something to seek in


def stop(outcome: str = "rolling") -> StopEvent:
    return StopEvent(0, 7, 2, "car", "elm", outcome, hold_s=1.6, min_speed=0.05, observed_s=4.2)  # type: ignore[arg-type]


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


def kept(store: Store, tmp_path: Path, *, snapshot: bool = True, clip: bool = True) -> int:
    """A stop event with its files in the evidence folder, as the vision service leaves them."""
    event_id = store.record_stop(stop(), BASE)
    folder = tmp_path / "evidence" / "2026-10-09"
    folder.mkdir(parents=True, exist_ok=True)
    if snapshot:
        (folder / f"stop-{event_id}.jpg").write_bytes(PICTURE)
    if clip:
        (folder / f"stop-{event_id}.mp4").write_bytes(CLIP)
    store.attach_evidence(
        event_id,
        f"evidence/2026-10-09/stop-{event_id}.jpg" if snapshot else None,
        f"evidence/2026-10-09/stop-{event_id}.mp4" if clip else None,
    )
    return event_id


def listed(client: TestClient) -> dict[str, object]:
    (row,) = client.get("/api/stops", params=RANGE).json()
    return row  # type: ignore[no-any-return]


def test_the_evidence_endpoints_need_a_login(app: FastAPI, store: Store, tmp_path: Path) -> None:
    event_id = kept(store, tmp_path)
    with TestClient(app) as anonymous:
        assert anonymous.get(f"/api/stops/{event_id}/snapshot.jpg").status_code == 401
        assert anonymous.get(f"/api/stops/{event_id}/clip.mp4").status_code == 401


def test_a_stop_with_evidence_says_where_to_get_it(
    client: TestClient, store: Store, tmp_path: Path
) -> None:
    event_id = kept(store, tmp_path)
    row = listed(client)
    assert row["snapshot_url"] == f"/api/stops/{event_id}/snapshot.jpg"
    assert row["clip_url"] == f"/api/stops/{event_id}/clip.mp4"
    assert client.get(str(row["snapshot_url"])).content == PICTURE
    assert client.get(str(row["clip_url"])).content == CLIP


def test_a_stop_without_evidence_has_nothing_to_get(client: TestClient, store: Store) -> None:
    store.record_stop(stop("stopped"), BASE)
    row = listed(client)
    assert row["snapshot_url"] is None and row["clip_url"] is None


def test_the_picture_is_an_image_that_the_browser_may_keep(
    client: TestClient, store: Store, tmp_path: Path
) -> None:
    event_id = kept(store, tmp_path)
    answer = client.get(f"/api/stops/{event_id}/snapshot.jpg")
    assert answer.status_code == 200 and answer.content == PICTURE
    assert answer.headers["content-type"] == "image/jpeg"
    assert answer.headers["cache-control"] == "private, max-age=86400"  # not no-store: it is kept
    assert answer.headers["content-disposition"] == f'inline; filename="stop-{event_id}.jpg"'


def test_the_clip_is_a_video_that_can_be_played_and_sought_in(
    client: TestClient, store: Store, tmp_path: Path
) -> None:
    event_id = kept(store, tmp_path)
    url = f"/api/stops/{event_id}/clip.mp4"
    whole = client.get(url)
    assert (
        whole.headers["content-type"] == "video/mp4" and whole.headers["accept-ranges"] == "bytes"
    )
    assert whole.headers["content-disposition"] == f'inline; filename="stop-{event_id}.mp4"'
    part = client.get(url, headers={"Range": "bytes=10-19"})
    assert part.status_code == 206 and part.content == CLIP[10:20]
    assert part.headers["content-range"] == f"bytes 10-19/{len(CLIP)}"
    tail = client.get(url, headers={"Range": "bytes=1000-"})
    assert tail.status_code == 206 and tail.content == CLIP[1000:]
    assert client.get(url, headers={"Range": "bytes=5000-6000"}).status_code == 416


def test_a_stop_that_has_only_a_picture_says_so(
    client: TestClient, store: Store, tmp_path: Path
) -> None:
    event_id = kept(store, tmp_path, clip=False)  # ffmpeg was not there, say
    row = listed(client)
    assert row["snapshot_url"] and row["clip_url"] is None
    assert client.get(f"/api/stops/{event_id}/snapshot.jpg").status_code == 200
    assert client.get(f"/api/stops/{event_id}/clip.mp4").status_code == 404


def test_evidence_that_has_been_deleted_is_no_longer_offered(
    client: TestClient, store: Store, tmp_path: Path
) -> None:
    event_id = kept(store, tmp_path)
    for path in (tmp_path / "evidence").rglob("*.*"):
        path.unlink()  # the clean-up removed it; the database still has the names
    row = listed(client)
    assert row["snapshot_url"] is None and row["clip_url"] is None
    assert client.get(f"/api/stops/{event_id}/snapshot.jpg").status_code == 404
    assert client.get(f"/api/stops/{event_id}/clip.mp4").status_code == 404


def test_there_is_no_evidence_for_what_is_not_a_stop_or_not_there(
    client: TestClient, store: Store, tmp_path: Path
) -> None:
    counted = store.record_count(CountEvent(0, 1, 2, "car", "main", 1), BASE)
    (tmp_path / "evidence").mkdir()
    (tmp_path / "evidence" / "count.jpg").write_bytes(PICTURE)
    store.attach_evidence(counted, "evidence/count.jpg", None)  # not a stop: refused
    plain = store.record_stop(stop("stopped"), BASE)
    for event_id in (counted, plain, 99_999):
        assert client.get(f"/api/stops/{event_id}/snapshot.jpg").status_code == 404
        assert client.get(f"/api/stops/{event_id}/clip.mp4").status_code == 404
    assert client.get("/api/stops/nine/snapshot.jpg").status_code == 422


@pytest.mark.parametrize(
    "where",
    [
        "../outside.jpg",  # up and out of the data directory
        "evidence/../outside.jpg",  # out of the evidence folder
        "evidence/../trafficpi.db",
        "{outside}",  # an absolute path
        "evidence/link.jpg",  # a link in the folder to a file outside it
        "evidence",  # a folder, not a file
        "",
    ],
)
def test_a_path_that_leaves_the_evidence_folder_is_never_served(
    client: TestClient, store: Store, tmp_path: Path, where: str
) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.jpg"  # beside the data directory
    outside.write_bytes(b"a secret")
    (tmp_path / "evidence").mkdir()
    (tmp_path / "evidence" / "link.jpg").symlink_to(outside)
    (tmp_path / "trafficpi.db").write_bytes(b"not a database")
    event_id = store.record_stop(stop(), BASE)
    store.attach_evidence(event_id, where.format(outside=outside), where.format(outside=outside))
    row = listed(client)
    assert row["snapshot_url"] is None and row["clip_url"] is None
    for kind in ("snapshot.jpg", "clip.mp4"):
        answer = client.get(f"/api/stops/{event_id}/{kind}")
        assert answer.status_code == 404 and b"secret" not in answer.content
    outside.unlink()


def test_a_review_keeps_the_evidence_in_the_answer(
    client: TestClient, store: Store, tmp_path: Path
) -> None:
    event_id = kept(store, tmp_path)
    answer = client.put(f"/api/stops/{event_id}/review", json={"state": "confirmed"}).json()
    assert answer["snapshot_url"] == f"/api/stops/{event_id}/snapshot.jpg"
    assert answer["clip_url"] == f"/api/stops/{event_id}/clip.mp4"
