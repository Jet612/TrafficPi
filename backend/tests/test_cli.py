import argparse

import pytest

from trafficpi.vision import cli


def test_size_parsing() -> None:
    assert cli._size("1280x720") == (1280, 720)
    assert cli._size("640X360") == (640, 360)
    with pytest.raises(argparse.ArgumentTypeError):
        cli._size("big")
    assert cli._size_or_auto("auto") is None
    assert cli._size_or_auto("2304x1296") == (2304, 1296)


def test_defaults_describe_a_fixed_roadside_view() -> None:
    args = cli.build_parser().parse_args(["dry-run"])
    settings = cli._camera_settings(args)
    assert settings.sensor_size == (2304, 1296)  # full field of view
    assert settings.lens_position == 0.0  # focus fixed at infinity
    assert settings.exposure_mode == "short"
    assert settings.channel_order == "rgb"
    assert (args.seconds, args.threshold, args.model) == (30, 0.25, "yolov8s")


def test_camera_options_are_passed_through() -> None:
    args = cli.build_parser().parse_args(
        [
            "record",
            "--sensor-size",
            "auto",
            "--lens-position",
            "auto",
            "--fps",
            "15",
            "--gain",
            "2",
            "--no-video",
        ]
    )
    settings = cli._camera_settings(args)
    assert settings.sensor_size is None and settings.lens_position is None
    assert (settings.frame_rate, settings.analogue_gain) == (15, 2)
    assert args.no_video and args.seconds == 60


def test_throttle_text() -> None:
    assert cli._flags(None) == "n/a"
    assert cli._flags(0) == "none"
    assert (
        cli._flags(0x50005)
        == "NOW: under-voltage, throttled; since boot: under-voltage occurred, throttling occurred"
    )
