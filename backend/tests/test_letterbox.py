import numpy as np
import pytest

from trafficpi.core.letterbox import PAD_VALUE, Letterbox


def test_wide_image_is_padded_top_and_bottom() -> None:
    lb = Letterbox.fit(640, 360, 640, 640)
    assert (lb.new_w, lb.new_h) == (640, 360)
    assert (lb.pad_x, lb.pad_y) == (0, 140)


def test_larger_image_is_scaled_down() -> None:
    lb = Letterbox.fit(1280, 720, 640, 640)
    assert (lb.new_w, lb.new_h) == (640, 360)
    assert (lb.pad_x, lb.pad_y) == (0, 140)


def test_tall_image_is_padded_left_and_right() -> None:
    lb = Letterbox.fit(360, 640, 640, 640)
    assert (lb.new_w, lb.new_h) == (360, 640)
    assert (lb.pad_x, lb.pad_y) == (140, 0)


def test_square_image_needs_no_padding() -> None:
    lb = Letterbox.fit(320, 320, 640, 640)
    assert (lb.new_w, lb.new_h, lb.pad_x, lb.pad_y) == (640, 640, 0, 0)


@pytest.mark.parametrize("size", [(0, 10, 10, 10), (10, -1, 10, 10), (10, 10, 0, 10)])
def test_rejects_non_positive_sizes(size: tuple[int, int, int, int]) -> None:
    with pytest.raises(ValueError):
        Letterbox.fit(*size)


def test_apply_centres_the_image_and_pads_with_grey() -> None:
    lb = Letterbox.fit(640, 360, 640, 640)
    image = np.random.default_rng(1).integers(0, 255, (360, 640, 3), dtype=np.uint8)
    canvas = lb.apply(image)
    assert canvas.shape == (640, 640, 3)
    assert (canvas[:140] == PAD_VALUE).all()
    assert (canvas[500:] == PAD_VALUE).all()
    assert (canvas[140:500] == image).all()


def test_apply_scales_with_area_averaging() -> None:
    lb = Letterbox.fit(1280, 720, 640, 640)
    image = np.full((720, 1280, 3), 200, dtype=np.uint8)
    canvas = lb.apply(image)
    assert (canvas[140:500] == 200).all()
    assert (canvas[:140] == PAD_VALUE).all()


def test_apply_into_a_reused_canvas_keeps_the_padding() -> None:
    lb = Letterbox.fit(640, 360, 640, 640)
    canvas = lb.new_canvas()
    for value in (10, 250):
        result = lb.apply(np.full((360, 640, 3), value, dtype=np.uint8), out=canvas)
        assert result is canvas
        assert (canvas[140:500] == value).all()
        assert (canvas[:140] == PAD_VALUE).all()


def test_apply_rejects_an_image_of_the_wrong_size() -> None:
    lb = Letterbox.fit(640, 360, 640, 640)
    with pytest.raises(ValueError):
        lb.apply(np.zeros((480, 640, 3), dtype=np.uint8))


def test_box_covering_the_image_region_maps_to_the_whole_source() -> None:
    lb = Letterbox.fit(640, 360, 640, 640)
    assert lb.box_to_source(0, 140 / 640, 1, 500 / 640) == pytest.approx((0, 0, 1, 1))


def test_box_maps_back_to_source_fractions() -> None:
    lb = Letterbox.fit(640, 360, 640, 640)
    x0, y0, x1, y1 = lb.box_to_source(0.1, 0.25, 0.3, 0.5)  # y: 160..320 of 640
    assert (x0, x1) == pytest.approx((0.1, 0.3))
    assert (y0, y1) == pytest.approx(((160 - 140) / 360, (320 - 140) / 360))


def test_box_in_the_padding_is_clipped_to_the_image_edge() -> None:
    lb = Letterbox.fit(640, 360, 640, 640)
    _, y0, _, y1 = lb.box_to_source(0.2, 0.0, 0.4, 0.1)  # rows 0..64, all padding
    assert y0 == y1 == 0.0


@pytest.mark.parametrize("src", [(640, 360), (1280, 720), (360, 640), (500, 500)])
def test_round_trip_through_the_canvas(src: tuple[int, int]) -> None:
    lb = Letterbox.fit(*src, 640, 640)
    rng = np.random.default_rng(7)
    for _ in range(50):
        x0, y0 = rng.uniform(0, 0.6, 2)
        x1, y1 = x0 + rng.uniform(0.05, 0.4), y0 + rng.uniform(0.05, 0.4)
        # source fractions -> canvas fractions, then back
        cx0, cx1 = (lb.pad_x + x0 * lb.new_w) / 640, (lb.pad_x + x1 * lb.new_w) / 640
        cy0, cy1 = (lb.pad_y + y0 * lb.new_h) / 640, (lb.pad_y + y1 * lb.new_h) / 640
        assert lb.box_to_source(cx0, cy0, cx1, cy1) == pytest.approx((x0, y0, x1, y1), abs=1e-9)
