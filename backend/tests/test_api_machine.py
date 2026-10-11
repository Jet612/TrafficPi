"""What the status page says about the machine around the services."""

import json
import os
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trafficpi.api import machine
from trafficpi.api.app import create_app
from trafficpi.api.auth import hash_password
from trafficpi.api.settings import ApiSettings
from trafficpi.api.vision_client import VisionClient
from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.store import Store

MB = 1_000_000


def write(path: Path, size: int, *, at: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    if at is not None:
        os.utime(path, (at, at))
    return path


# --- how much the data directory holds ---------------------------------------------------------


def test_an_empty_data_directory_holds_nothing(tmp_path: Path) -> None:
    assert machine.data_usage(tmp_path / "nowhere") == {
        "database_mb": 0.0,
        "evidence_mb": 0.0,
        "backups_mb": 0.0,
        "evidence_files": 0,
        "backup_files": 0,
    }


def test_the_database_with_its_wal_and_the_evidence_and_the_backups_are_added_up(
    tmp_path: Path,
) -> None:
    write(tmp_path / "trafficpi.db", 2 * MB)
    write(tmp_path / "trafficpi.db-wal", MB // 2)
    write(tmp_path / "trafficpi.db-shm", 32_768)
    write(tmp_path / "evidence" / "2026-10-09" / "stop-1.jpg", 35_000)
    write(tmp_path / "evidence" / "2026-10-09" / "stop-1.mp4", 50_000)
    write(tmp_path / "evidence" / "2026-10-08" / "stop-0.jpg", 35_000)
    write(tmp_path / "backups" / "trafficpi-backup-20261009-033000.tar.gz", 3 * MB)
    write(tmp_path / "backups" / "trafficpi-backup-20261008-033000.tar.gz", 3 * MB)
    write(tmp_path / "backups" / "notes.txt", 10 * MB)  # not a backup: not counted
    write(tmp_path / "demo" / "trafficpi.db", 50 * MB)  # not this installation's
    usage = machine.data_usage(tmp_path)
    assert usage["database_mb"] == pytest.approx(2.5, abs=0.05)
    assert usage["evidence_mb"] == pytest.approx(0.1, abs=0.05) and usage["evidence_files"] == 3
    assert usage["backups_mb"] == pytest.approx(6.0, abs=0.05) and usage["backup_files"] == 2


def test_links_in_the_evidence_folder_are_not_followed(tmp_path: Path) -> None:
    outside = write(tmp_path / "outside" / "big.bin", 5 * MB)
    (tmp_path / "evidence").mkdir()
    (tmp_path / "evidence" / "link.bin").symlink_to(outside)
    (tmp_path / "evidence" / "folder-link").symlink_to(tmp_path / "outside")
    assert machine.data_usage(tmp_path)["evidence_files"] == 0


# --- the newest backup -------------------------------------------------------------------------


def test_the_newest_backup_is_told_with_its_age_size_and_how_many_there_are(tmp_path: Path) -> None:
    folder = tmp_path / "backups"
    write(folder / "trafficpi-backup-20261007-033000.tar.gz", MB, at=1_000_000_000)
    newest = write(folder / "trafficpi-backup-20261009-033000.tar.gz", 2 * MB, at=1_700_000_000)
    write(folder / "trafficpi-backup-20261008-033000.tar.gz", MB, at=1_500_000_000)
    write(folder / ".trafficpi-backup-20261010-033000.tar.gz.partial", MB, at=1_800_000_000)
    found = machine.newest_backup(tmp_path)
    assert found == {"at_ms": 1_700_000_000_000, "size_mb": 2.0, "count": 3}
    assert newest.exists()


def test_without_a_backup_there_is_none_to_tell(tmp_path: Path) -> None:
    assert machine.newest_backup(tmp_path) is None
    write(tmp_path / "backups" / "something-else.tar.gz", 100)
    assert machine.newest_backup(tmp_path) is None


# --- the memory test ---------------------------------------------------------------------------


def test_the_result_of_the_installers_memory_test_is_read(tmp_path: Path) -> None:
    (tmp_path / "memtest.json").write_text(
        json.dumps({"at": "2026-10-09T19:22:00Z", "mb": 512, "passes": 1, "ok": True})
    )
    assert machine.memtest_result(tmp_path) == {"at_ms": 1_791_573_720_000, "mb": 512, "ok": True}
    (tmp_path / "memtest.json").write_text(
        json.dumps({"at": "2026-10-09T19:22:00Z", "mb": 64, "passes": 1, "ok": False})
    )
    assert machine.memtest_result(tmp_path) == {"at_ms": 1_791_573_720_000, "mb": 64, "ok": False}


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        "{}",
        '{"at": "yesterday", "mb": 512, "ok": true}',
        '{"at": "2026-10-09T19:22:00Z", "mb": "512", "ok": true}',
        '{"at": "2026-10-09T19:22:00Z", "mb": 512, "ok": "yes"}',
    ],
)
def test_a_memory_test_file_that_cannot_be_read_is_no_result(tmp_path: Path, content: str) -> None:
    (tmp_path / "memtest.json").write_text(content)
    assert machine.memtest_result(tmp_path) is None


def test_no_memory_test_file_is_no_result(tmp_path: Path) -> None:
    assert machine.memtest_result(tmp_path) is None


# --- the cache and the status endpoint ---------------------------------------------------------


def test_the_sizes_are_not_worked_out_again_within_the_time_to_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [100.0]
    cache = machine.TtlCache(30.0, clock=lambda: now[0])
    calls: list[int] = []
    monkeypatch.setattr(
        machine, "data_usage", lambda folder: calls.append(1) or {"database_mb": 1.0}
    )
    assert cache.usage(tmp_path) == {"database_mb": 1.0}
    now[0] = 129.0
    cache.usage(tmp_path)
    assert len(calls) == 1
    now[0] = 131.0
    cache.usage(tmp_path)
    assert len(calls) == 2


@pytest.fixture(scope="module")
def password_hash() -> str:
    return hash_password("correct horse battery")


def a_vision_service(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/state":
        return httpx.Response(
            200,
            json={
                "camera": {"ok": True, "error": None},
                "fps": 30.0,
                "uptime_s": 120.0,
                "profile_version": 3,
                "detector": "yolov8s_h8l",
                "accelerator": {
                    "architecture": "HAILO8L",
                    "firmware": "4.23.0",
                    "model": "yolov8s_h8l",
                    "inference_ms": 17.4,
                    "inference_p95_ms": 17.9,
                },
                "system": {"cpu_percent": 20.0},
            },
        )
    return httpx.Response(404)


@pytest.fixture
def client(tmp_path: Path, password_hash: str) -> Iterator[TestClient]:
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    store = Store(engine)
    store.ensure_camera()
    store.create_user("admin", password_hash)
    vision = VisionClient("http://vision", transport=httpx.MockTransport(a_vision_service))
    app: FastAPI = create_app(
        ApiSettings(data_dir=tmp_path, dashboard_dir=None), store=store, vision=vision
    )
    with TestClient(app, base_url="http://testserver") as c:
        assert (
            c.post(
                "/api/auth/login", json={"username": "admin", "password": "correct horse battery"}
            ).status_code
            == 204
        )
        yield c


def test_the_status_tells_about_the_chip_the_services_the_data_the_backup_and_the_memory(
    client: TestClient, tmp_path: Path
) -> None:
    write(tmp_path / "trafficpi.db", MB)
    write(tmp_path / "backups" / "trafficpi-backup-20261009-033000.tar.gz", MB, at=1_700_000_000)
    (tmp_path / "memtest.json").write_text(
        json.dumps({"at": "2026-10-09T19:22:00Z", "mb": 512, "passes": 1, "ok": True})
    )
    body = client.get("/api/status").json()
    assert body["vision"]["accelerator"] == {
        "architecture": "HAILO8L",
        "firmware": "4.23.0",
        "model": "yolov8s_h8l",
        "inference_ms": 17.4,
        "inference_p95_ms": 17.9,
    }
    assert body["api_uptime_s"] >= 0
    assert body["data"]["database_mb"] == pytest.approx(1.0, abs=0.05)
    assert body["last_backup"] == {"at_ms": 1_700_000_000_000, "size_mb": 1.0, "count": 1}
    assert body["memory_test"] == {"at_ms": 1_791_573_720_000, "mb": 512, "ok": True}


def test_a_fresh_install_has_no_backup_and_no_memory_test_to_tell(client: TestClient) -> None:
    body = client.get("/api/status").json()
    assert body["last_backup"] is None and body["memory_test"] is None
    assert body["data"]["evidence_files"] == 0


def test_an_older_vision_service_that_says_nothing_of_the_chip_still_gives_a_status(
    tmp_path: Path, password_hash: str
) -> None:
    def old(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"camera": {"ok": True}, "fps": 30.0, "detector": "x"})

    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    store = Store(engine)
    store.ensure_camera()
    store.create_user("admin", password_hash)
    vision = VisionClient("http://vision", transport=httpx.MockTransport(old))
    app = create_app(ApiSettings(data_dir=tmp_path, dashboard_dir=None), store=store, vision=vision)
    with TestClient(app) as c:
        c.post("/api/auth/login", json={"username": "admin", "password": "correct horse battery"})
        assert c.get("/api/status").json()["vision"]["accelerator"] == {
            "architecture": None,
            "firmware": None,
            "model": None,
            "inference_ms": None,
            "inference_p95_ms": None,
        }
