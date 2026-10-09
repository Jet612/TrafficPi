"""COCO class names (the 80-class list the Hailo YOLO models are trained on)."""

from __future__ import annotations

COCO_NAMES: tuple[str, ...] = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog",
    "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle",
    "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch", "potted plant",
    "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone",
    "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase", "scissors",
    "teddy bear", "hair drier", "toothbrush",
)  # fmt: skip

# Classes that matter for traffic monitoring. Everything else the model can detect is noise here.
# Vehicles: bicycle (1), car (2), motorcycle (3), bus (5), truck (7). Traffic adds person (0).
VEHICLE_CLASS_IDS: frozenset[int] = frozenset({1, 2, 3, 5, 7})
TRAFFIC_CLASS_IDS: frozenset[int] = VEHICLE_CLASS_IDS | {0}


def class_name(class_id: int) -> str:
    """Human-readable name for a COCO class id, or ``classN`` if it is out of range."""
    if 0 <= class_id < len(COCO_NAMES):
        return COCO_NAMES[class_id]
    return f"class{class_id}"
