import numpy as np
import pytest

from trafficpi.core.coco import COCO_NAMES, class_name
from trafficpi.core.letterbox import Letterbox
from trafficpi.vision.detector import parse_nms_output

LB = Letterbox.fit(640, 360, 640, 640)  # 140 px of padding above and below


def raw_output(**rows: list[list[float]]) -> list[np.ndarray]:
    """80 per-class arrays like the chip returns; ``c2=[[y0, x0, y1, x1, score]]`` fills class 2."""
    per_class = [np.zeros((0, 5)) for _ in COCO_NAMES]
    for key, value in rows.items():
        per_class[int(key[1:])] = np.array(value, dtype=np.float64)
    return per_class


def test_names() -> None:
    assert len(COCO_NAMES) == 80
    assert (class_name(0), class_name(2), class_name(5), class_name(7)) == (
        "person",
        "car",
        "bus",
        "truck",
    )
    assert class_name(99) == "class99"


def test_keeps_traffic_classes_above_the_threshold_best_first() -> None:
    raw = raw_output(
        c2=[[0.25, 0.1, 0.5, 0.3, 0.60], [0.3, 0.5, 0.6, 0.7, 0.90]],
        c0=[[0.3, 0.8, 0.5, 0.85, 0.50]],
        c7=[[0.3, 0.2, 0.5, 0.4, 0.10]],  # truck below the threshold
    )
    found = parse_nms_output(raw, LB, threshold=0.25)
    assert [(d.label, d.score) for d in found] == [("car", 0.9), ("car", 0.6), ("person", 0.5)]


def test_boxes_are_mapped_into_source_coordinates() -> None:
    (car,) = parse_nms_output(raw_output(c2=[[0.25, 0.1, 0.5, 0.3, 0.8]]), LB)
    assert (car.x0, car.x1) == pytest.approx((0.1, 0.3))
    assert (car.y0, car.y1) == pytest.approx(((160 - 140) / 360, (320 - 140) / 360))
    assert car.class_id == 2


def test_other_classes_are_dropped_unless_all_are_requested() -> None:
    raw = raw_output(c15=[[0.3, 0.3, 0.5, 0.5, 0.9]])  # a cat
    assert parse_nms_output(raw, LB) == []
    (cat,) = parse_nms_output(raw, LB, class_ids=None)
    assert cat.label == "cat"


def test_a_box_entirely_in_the_padding_is_dropped() -> None:
    assert parse_nms_output(raw_output(c2=[[0.0, 0.2, 0.1, 0.4, 0.9]]), LB) == []


def test_empty_output() -> None:
    assert parse_nms_output(raw_output(), LB) == []


def test_accepts_dict_and_batch_wrappers() -> None:
    raw = raw_output(c2=[[0.25, 0.1, 0.5, 0.3, 0.8]])
    assert len(parse_nms_output({"yolov8s/nms": raw}, LB)) == 1
    assert len(parse_nms_output([raw], LB)) == 1


def test_detection_geometry_helpers() -> None:
    (car,) = parse_nms_output(raw_output(c2=[[0.25, 0.1, 0.5, 0.3, 0.8]]), LB)
    assert car.width == pytest.approx(0.2)
    assert car.center[0] == pytest.approx(0.2)
    assert car.bottom_center == (car.center[0], car.y1)
