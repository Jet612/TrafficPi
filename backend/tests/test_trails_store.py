from pathlib import Path

import pytest

from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.store import Store
from trafficpi.vision.trails import FinishedTrail, decode_path, encode_path

HOUR_MS = 3_600_000
BASE = 1_760_000_000_000


def trail(
    at_ms: int, *, label: str = "car", seconds: float = 5.0, x0: float = 0.2, track_id: int = 1
) -> FinishedTrail:
    """A vehicle that drove left to right for ``seconds``, starting at ``at_ms``."""
    n = int(seconds * 5) + 1
    offsets = [round(i * 1000 / 5) for i in range(n)]
    xs = [x0 + 0.6 * i / (n - 1) for i in range(n)]
    ys = [0.6] * n
    return FinishedTrail(
        track_id=track_id,
        started_at=at_ms,
        ended_at=at_ms + offsets[-1],
        label=label,
        points=n,
        extent=0.6,
        path=encode_path(offsets, xs, ys),
    )


@pytest.fixture
def store() -> Store:
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    s = Store(engine)
    s.ensure_camera("Front", now_ms=BASE)
    return s


def test_a_stored_trail_comes_back_with_its_path_intact(store: Store) -> None:
    store.add_trails([trail(BASE + 1_000, label="truck", x0=0.1)])
    (row,) = store.trails_between(BASE, BASE + HOUR_MS)
    assert (row.class_label, row.started_at, row.points) == ("truck", BASE + 1_000, 26)
    assert row.ended_at == BASE + 6_000 and row.extent == 0.6
    path = decode_path(row.path)
    assert len(path) == 26
    assert (path.x[0], path.x[-1]) == pytest.approx((0.1, 0.7), abs=1e-4)
    assert path.offsets_ms[-1] == 5_000


def test_trails_come_back_oldest_first_and_only_those_that_began_in_the_range(store: Store) -> None:
    store.add_trails([trail(BASE + 30_000, track_id=3), trail(BASE + 10_000, track_id=1)])
    store.add_trails([trail(BASE + 20_000, track_id=2), trail(BASE + HOUR_MS)])
    rows = store.trails_between(BASE, BASE + HOUR_MS)
    assert [r.started_at - BASE for r in rows] == [10_000, 20_000, 30_000]  # the last one is at 1 h
    # the range is closed at its start and open at its end
    assert [r.started_at - BASE for r in store.trails_between(BASE + 20_000, BASE + 30_000)] == [
        20_000
    ]
    assert store.trails_between(BASE + 31_000, BASE + HOUR_MS) == []


def test_trails_can_be_asked_for_by_kind_and_are_limited(store: Store) -> None:
    store.add_trails(
        [
            trail(BASE + i * 1_000, label=label)
            for i, label in enumerate(["car", "bus", "car", "bicycle"])
        ]
    )
    assert [r.class_label for r in store.trails_between(BASE, BASE + HOUR_MS, labels={"car"})] == [
        "car",
        "car",
    ]
    assert {
        r.class_label for r in store.trails_between(BASE, BASE + HOUR_MS, labels=["bus", "bicycle"])
    } == {
        "bus",
        "bicycle",
    }
    assert store.trails_between(BASE, BASE + HOUR_MS, labels=[]) == []
    assert len(store.trails_between(BASE, BASE + HOUR_MS, limit=2)) == 2


def test_counting_trails_in_a_range(store: Store) -> None:
    store.add_trails([trail(BASE + i * HOUR_MS // 2) for i in range(6)])  # one every half hour
    assert store.trail_count() == 6
    assert store.trail_count(BASE + HOUR_MS) == 4
    assert store.trail_count(end_ms=BASE + HOUR_MS) == 2
    assert store.trail_count(BASE + HOUR_MS, BASE + 2 * HOUR_MS) == 2


def test_adding_nothing_is_fine(store: Store) -> None:
    store.add_trails([])
    assert store.trail_count() == 0


def test_each_camera_sees_only_its_own_trails() -> None:
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    front, side = Store(engine), Store(engine, camera_id="side")
    front.ensure_camera("Front", now_ms=BASE)
    side.ensure_camera("Side", now_ms=BASE)
    front.add_trails([trail(BASE)])
    side.add_trails([trail(BASE + 1, track_id=7), trail(BASE + 2, track_id=8)])
    assert front.trail_count() == 1
    assert side.trail_count() == 2
    assert len(side.trails_between(BASE, BASE + HOUR_MS)) == 2
    assert side.delete_trails_before(BASE + HOUR_MS) == 2
    assert front.trail_count() == 1  # untouched


def test_old_trails_are_deleted_in_chunks_and_newer_ones_stay(store: Store) -> None:
    store.add_trails([trail(BASE + i * 60_000) for i in range(10)])  # ten, a minute apart
    cutoff = BASE + 6 * 60_000  # the first six ended before this (each lasts five seconds)
    assert store.delete_trails_before(cutoff, limit=4) == 4  # a chunk
    assert store.trail_count() == 6
    assert store.delete_trails_before(cutoff, limit=4) == 2  # what is left of the old ones
    assert store.delete_trails_before(cutoff, limit=4) == 0  # nothing more to do
    assert [r.started_at for r in store.trails_between(BASE, BASE + HOUR_MS)] == [
        BASE + i * 60_000 for i in range(6, 10)
    ]


def test_the_oldest_go_first_when_a_chunk_is_smaller_than_the_backlog(store: Store) -> None:
    store.add_trails([trail(BASE + i * 60_000) for i in (4, 1, 3, 0, 2)])
    store.delete_trails_before(BASE + HOUR_MS, limit=2)
    assert [r.started_at for r in store.trails_between(BASE, BASE + HOUR_MS)] == [
        BASE + i * 60_000 for i in (2, 3, 4)
    ]


def test_a_trail_that_ended_after_the_cutoff_is_kept_even_if_it_began_before(store: Store) -> None:
    store.add_trails([trail(BASE, seconds=10)])  # ends at BASE + 10 s
    assert store.delete_trails_before(BASE + 5_000) == 0
    assert store.delete_trails_before(BASE + 11_000) == 1


def test_trails_survive_a_file_database_and_a_new_connection(tmp_path: Path) -> None:
    engine = create_db_engine(tmp_path / "t.db")
    upgrade_to_head(engine)
    first = Store(engine)
    first.ensure_camera("Front", now_ms=BASE)
    first.add_trails([trail(BASE)])
    first.close()
    engine = create_db_engine(tmp_path / "t.db")
    assert Store(engine).trail_count() == 1
