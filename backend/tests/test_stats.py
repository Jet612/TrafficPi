from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from trafficpi.api import stats
from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.store import Store
from trafficpi.vision.rules import CountEvent

NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def ms(year: int, month: int, day: int, hour: int = 0, minute: int = 0, tz: ZoneInfo = NY) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=tz).timestamp() * 1000)


@pytest.fixture
def store() -> Store:
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    s = Store(engine)
    s.ensure_camera()
    return s


def count(store: Store, when_ms: int, *, line="main", direction=1, label="car") -> None:  # type: ignore[no-untyped-def]
    store.record_count(CountEvent(0, 1, 2, label, line, direction), when_ms)


def test_day_bounds_follow_the_local_calendar() -> None:
    start, end = stats.day_bounds(ms(2026, 10, 8, 13, 30), NY)
    assert start * 1000 == ms(2026, 10, 8) and end * 1000 == ms(2026, 10, 9)
    # 23 hours on the day clocks go forward, 25 when they go back
    spring = stats.day_bounds(ms(2026, 3, 8, 12), NY)
    autumn = stats.day_bounds(ms(2026, 11, 1, 12), NY)
    assert (spring[1] - spring[0]) // 3600 == 23 and (autumn[1] - autumn[0]) // 3600 == 25


def test_summarize_by_class_and_line(store: Store) -> None:
    count(store, ms(2026, 10, 8, 9, 5))
    count(store, ms(2026, 10, 8, 9, 6), label="truck")
    count(store, ms(2026, 10, 8, 9, 7), direction=-1)
    count(store, ms(2026, 10, 8, 9, 8), line="side", label="bus")
    rows = store.minute_counts(0, 2**40)
    summary = stats.summarize(rows, {"main": "Main road"})
    assert summary["total"] == 4 and summary["by_class"] == {"car": 2, "truck": 1, "bus": 1}
    main, side = summary["lines"]
    assert main["id"] == "main" and main["name"] == "Main road"
    assert (main["forward"], main["reverse"]) == (2, 1)
    assert main["by_class"] == {"car": 2, "truck": 1}
    assert side["id"] == "side" and side["name"] == "" and side["forward"] == 1


def test_every_configured_line_is_listed_even_with_no_counts(store: Store) -> None:
    count(store, ms(2026, 10, 8, 9, 5), line="gone")  # counted once, since removed from the profile
    summary = stats.summarize(
        store.minute_counts(0, 2**40), {"north": "Northbound", "south": "Southbound"}
    )
    assert [(x["id"], x["name"], x["forward"] + x["reverse"]) for x in summary["lines"]] == [
        ("gone", "", 1),
        ("north", "Northbound", 0),
        ("south", "Southbound", 0),
    ]
    assert summary["lines"][1]["by_class"] == {}
    empty = stats.today_summary(store, NY, ms(2026, 10, 9, 15), {"north": "Northbound"})
    assert empty["total"] == 0 and [x["id"] for x in empty["lines"]] == ["north"]


def test_today_uses_the_local_day_not_the_utc_day(store: Store) -> None:
    count(store, ms(2026, 10, 8, 0, 30))  # 00:30 local on the 8th is 04:30 UTC
    count(store, ms(2026, 10, 7, 23, 30))  # 23:30 local on the 7th is the next day in UTC
    count(store, ms(2026, 10, 9, 0, 0))  # midnight: tomorrow
    today = stats.today_summary(store, NY, ms(2026, 10, 8, 15))
    assert today["date"] == "2026-10-08" and today["timezone"] == "America/New_York"
    assert today["total"] == 1
    assert len(today["hourly"]) == 24 and today["hourly"][0] == {
        "t": ms(2026, 10, 8),
        "hour": 0,
        "count": 1,
    }
    assert sum(h["count"] for h in today["hourly"]) == 1


def test_hourly_series_includes_empty_hours(store: Store) -> None:
    for minute in (5, 10, 59):
        count(store, ms(2026, 10, 8, 9, minute))
    count(store, ms(2026, 10, 8, 11, 0))
    start, end = ms(2026, 10, 8, 8) // 1000, ms(2026, 10, 8, 12) // 1000
    rows = store.minute_counts(start, end)
    result = stats.series(rows, NY, start, end, "hour")
    assert [(r["local"][11:13], r["total"]) for r in result] == [
        ("08", 0),
        ("09", 3),
        ("10", 0),
        ("11", 1),
    ]
    assert result[1]["by_class"] == {"car": 3} and result[1]["by_line"] == {
        "main": {"forward": 3, "reverse": 0}
    }


def test_daily_series_and_minute_series(store: Store) -> None:
    count(store, ms(2026, 10, 7, 23, 59))
    count(store, ms(2026, 10, 8, 0, 1))
    count(store, ms(2026, 10, 8, 0, 1))
    start, end = ms(2026, 10, 7) // 1000, ms(2026, 10, 9) // 1000
    daily = stats.series(store.minute_counts(start, end), NY, start, end, "day")
    assert [d["total"] for d in daily] == [1, 2] and daily[0]["local"].startswith(
        "2026-10-07T00:00"
    )
    minutes = stats.series(
        store.minute_counts(start, end),
        NY,
        ms(2026, 10, 8, 0, 0) // 1000,
        ms(2026, 10, 8, 0, 3) // 1000,
        "minute",
    )
    assert [m["total"] for m in minutes] == [0, 2, 0]


def test_the_hour_that_repeats_when_clocks_go_back_appears_twice(store: Store) -> None:
    # 2026-11-01: at 02:00 EDT clocks go back to 01:00 EST, so local 01:xx happens twice.
    first = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)  # 01:30 EDT
    second = datetime(2026, 11, 1, 6, 30, tzinfo=UTC)  # 01:30 EST
    count(store, int(first.timestamp() * 1000))
    count(store, int(second.timestamp() * 1000))
    count(store, int(second.timestamp() * 1000))
    start, end = stats.day_bounds(ms(2026, 11, 1, 12), NY)
    result = stats.series(store.minute_counts(start, end), NY, start, end, "hour")
    assert len(result) == 25
    ones = [r["total"] for r in result if r["local"][11:13] == "01"]
    assert ones == [1, 2]
