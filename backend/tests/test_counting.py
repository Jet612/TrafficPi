from trafficpi.core.coco import class_name
from trafficpi.core.geometry import Line
from trafficpi.vision.rules import CountEvent, CountingLine, CountingRule
from trafficpi.vision.tracker import TrackUpdate

# A line facing right on screen at y = 0.5: top-to-bottom crossings are +1, bottom-to-top are -1.
LINE = CountingLine("main", Line((0.2, 0.5), (0.8, 0.5)), name="Main")
Path = list[tuple[float, float]]


def track(
    track_id: int, x: float, y: float, *, confirmed: bool = True, cls: int = 2, h: float = 0.06
) -> TrackUpdate:
    """A track whose ground point (bottom centre of its box) is ``(x, y)``."""
    box = (x - 0.03, y - h, x + 0.03, y)
    return TrackUpdate(track_id, cls, class_name(cls), 0.9, box, (0.0, 0.0), 10, 1.0, confirmed)


def walk(rule: CountingRule, path: Path, track_id: int = 1, **kw: float | bool) -> list[int]:
    """Feed a path one frame at a time; return the directions of the counts."""
    directions = []
    for i, (x, y) in enumerate(path):
        for event in rule.update(i, [track(track_id, x, y, **kw)]):  # type: ignore[arg-type]
            assert isinstance(event, CountEvent)
            directions.append(event.direction)
    return directions


def down(x: float = 0.5, *, start: float = 0.4, stop: float = 0.6, step: float = 0.02) -> Path:
    return [(x, start + step * i) for i in range(round((stop - start) / step) + 1)]


def test_a_crossing_top_to_bottom_counts_once_as_plus_one() -> None:
    assert walk(CountingRule([LINE]), down()) == [1]


def test_a_crossing_bottom_to_top_counts_minus_one() -> None:
    assert walk(CountingRule([LINE]), list(reversed(down()))) == [-1]


def test_event_details() -> None:
    rule = CountingRule([LINE])
    events: list[object] = []
    for i, (x, y) in enumerate(down()):
        events += rule.update(1000 + i, [track(7, x, y, cls=7)])
    (event,) = events
    assert isinstance(event, CountEvent)
    assert (event.track_id, event.line_id, event.direction, event.label) == (7, "main", 1, "truck")
    # y = 0.52 (index 6) is the first point clearly past the line; it must then persist one frame.
    assert event.timestamp_ns == 1000 + 7


def test_jitter_inside_the_margin_counts_nothing() -> None:
    path = [(0.5, 0.45)] + [(0.5, 0.5 + (0.002 if i % 2 else -0.002)) for i in range(20)]
    assert walk(CountingRule([LINE]), path) == []


def test_jitter_after_crossing_does_not_count_again() -> None:
    path = [(0.5, 0.45), (0.5, 0.52), (0.5, 0.53)]
    path += [(0.5, 0.5 + (0.003 if i % 2 else -0.003)) for i in range(20)]
    assert walk(CountingRule([LINE]), path) == [1]


def test_a_single_frame_excursion_across_the_line_does_not_count() -> None:
    path = [(0.5, 0.45)] * 3 + [(0.5, 0.53)] + [(0.5, 0.45)] * 3
    assert walk(CountingRule([LINE]), path) == []


def test_hesitating_in_the_band_restarts_the_persistence_count() -> None:
    path = [(0.5, 0.45), (0.5, 0.52), (0.5, 0.503), (0.5, 0.52)]  # across, back in the band, across
    assert walk(CountingRule([LINE]), path) == []
    assert walk(CountingRule([LINE]), [*path, (0.5, 0.53)], track_id=2) == [1]  # now it holds


def test_going_back_and_forth_counts_each_direction_once() -> None:
    ys = [0.45, 0.45, 0.55, 0.55, 0.45, 0.45, 0.55, 0.55]
    assert walk(CountingRule([LINE]), [(0.5, y) for y in ys]) == [1, -1]


def test_passing_around_the_end_of_the_line_is_not_a_crossing() -> None:
    assert walk(CountingRule([LINE]), down(x=0.9)) == []
    assert walk(CountingRule([LINE]), down(x=0.1), track_id=2) == []


def test_a_crossing_just_past_the_end_still_counts_within_the_tolerance() -> None:
    assert walk(CountingRule([LINE]), down(x=0.805)) == [1]
    assert walk(CountingRule([LINE]), down(x=0.85), track_id=2) == []


def test_a_diagonal_path_through_the_segment_counts() -> None:
    path = [(0.3 + 0.04 * i, 0.4 + 0.02 * i) for i in range(11)]  # passes y = 0.5 at x = 0.5
    assert walk(CountingRule([LINE]), path) == [1]


def test_a_track_that_starts_on_the_far_side_is_not_a_crossing() -> None:
    assert walk(CountingRule([LINE]), [(0.5, 0.7), (0.5, 0.8), (0.5, 0.9)]) == []


def test_a_track_first_seen_on_the_line_has_no_previous_side() -> None:
    assert walk(CountingRule([LINE]), [(0.5, 0.5), (0.5, 0.52), (0.5, 0.6)]) == []


def test_an_unconfirmed_track_is_not_counted() -> None:
    assert walk(CountingRule([LINE]), down(), confirmed=False) == []


def test_only_the_selected_classes_count() -> None:
    cars_only = CountingLine("cars", LINE.line, classes=frozenset({2}))
    assert walk(CountingRule([cars_only]), down(), cls=0) == []
    assert walk(CountingRule([cars_only]), down(), cls=2, track_id=2) == [1]


def test_the_margin_grows_with_the_size_of_the_object() -> None:
    path = [(0.5, 0.46)] * 2 + [(0.5, 0.54)] * 3  # ends 0.04 beyond the line
    assert walk(CountingRule([LINE]), path, h=0.06) == [1]  # margin 0.006: clearly across
    assert walk(CountingRule([LINE]), path, track_id=2, h=0.6) == []  # margin 0.06: still undecided


def test_the_centre_point_can_be_used_instead_of_the_ground_point() -> None:
    ground, centre = (
        CountingRule([LINE]),
        CountingRule([CountingLine("c", LINE.line, point="center")]),
    )
    path = down(
        start=0.4, stop=0.7
    )  # the box is 0.06 tall: its centre is 0.03 above the ground point

    def first_count(rule: CountingRule) -> int:
        return next(i for i, (x, y) in enumerate(path) if rule.update(i, [track(1, x, y)]))

    assert first_count(centre) > first_count(ground)


def test_two_lines_count_independently() -> None:
    upper = CountingLine("upper", Line((0.2, 0.4), (0.8, 0.4)))
    rule = CountingRule([LINE, upper])
    events = []
    for i, (x, y) in enumerate(down(start=0.3, stop=0.7)):
        events += rule.update(i, [track(1, x, y)])
    assert [(e.line_id, e.direction) for e in events if isinstance(e, CountEvent)] == [
        ("upper", 1),
        ("main", 1),
    ]


def test_several_tracks_in_one_frame() -> None:
    rule = CountingRule([LINE])
    seen = []
    for i in range(6):
        frame = [track(1, 0.4, 0.4 + 0.04 * i), track(2, 0.6, 0.6 - 0.04 * i)]
        seen += [
            (e.track_id, e.direction) for e in rule.update(i, frame) if isinstance(e, CountEvent)
        ]
    assert sorted(seen) == [(1, 1), (2, -1)]


def test_forgetting_a_track_clears_its_memory() -> None:
    rule = CountingRule([LINE])
    rule.update(0, [track(1, 0.5, 0.45)])
    rule.forget([1])
    assert rule.update(1, [track(1, 0.5, 0.55)]) == []  # nothing remembered to cross from
