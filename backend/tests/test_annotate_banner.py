"""The lines of text at the top of an annotated picture: one clean line each, easy to read."""

import cv2
import numpy as np

from trafficpi.vision.annotate import BANNER_ROW, BANNER_SCALE, BANNER_SHADE, FONT, draw
from trafficpi.vision.engine import CountTotals

GRAY = 90
CAPTION = "Elm Street: car #50 did not stop - 2026-10-09 04:32:45"


def picture(width: int = 640, height: int = 360) -> np.ndarray:
    return np.full((height, width, 3), GRAY, np.uint8)


def with_banners(*banners: str, width: int = 640) -> np.ndarray:
    image = picture(width)
    draw(image, lines=[], tracks=[], trails={}, banners=list(banners), totals=CountTotals())
    return image


def white_columns(image: np.ndarray, top: int, bottom: int) -> np.ndarray:
    """The columns that have lettering in them, within some rows."""
    return np.flatnonzero((image[top:bottom] > 200).all(axis=2).any(axis=0))


def test_a_long_line_is_drawn_once_with_no_ghost_of_it_beyond_its_own_width() -> None:
    image = with_banners(CAPTION)
    text_w = cv2.getTextSize(CAPTION, FONT, BANNER_SCALE, 1)[0][0]
    columns = white_columns(image, 0, BANNER_ROW + 4)
    assert columns.size > 0
    assert columns.max() <= 10 + text_w + 1  # a thicker outline pass ran 28 pixels past this


def test_the_strip_behind_the_text_is_darker_than_the_picture_and_only_that() -> None:
    image = with_banners(CAPTION)
    text_w = cv2.getTextSize(CAPTION, FONT, BANNER_SCALE, 1)[0][0]
    shaded = round(GRAY * (1 - BANNER_SHADE))
    assert image[5, 3].tolist() == [shaded] * 3  # under the text
    assert image[4 + BANNER_ROW + 1, 3].tolist() == [GRAY] * 3  # below the strip: the picture
    assert image[5, text_w + 30].tolist() == [GRAY] * 3  # beside the strip: the picture
    assert image[300, 300].tolist() == [GRAY] * 3  # and the rest of it


def test_each_line_has_a_strip_of_its_own_and_none_is_darkened_twice() -> None:
    image = with_banners("first", "second", "third")
    shaded = [GRAY * (1 - BANNER_SHADE)] * 3
    for row in range(3):
        top = 4 + BANNER_ROW * row
        for y in (top, top + BANNER_ROW - 1):  # the first and the last row of its strip
            assert np.allclose(image[y, 2], shaded, atol=1), (row, y)
    assert image[4 + BANNER_ROW * 3 + 1, 2].tolist() == [GRAY] * 3  # nothing under the last one


def test_a_line_wider_than_the_picture_is_made_smaller_to_fit() -> None:
    long = (
        "Main Street and Fifth Avenue, northbound lane: a truck did not stop - 2026-10-09 04:32:45"
    )
    assert cv2.getTextSize(long, FONT, BANNER_SCALE, 1)[0][0] > 640  # it would run off the picture
    image = with_banners(long)
    columns = white_columns(image, 0, BANNER_ROW + 4)
    assert columns.size > 0 and columns.max() < 640 - 5
    assert columns.min() >= 8


def test_a_line_that_cannot_fit_even_when_smaller_ends_at_the_picture() -> None:
    image = with_banners("x" * 400)  # nothing could make this fit; it must not fail either
    assert image.shape == (360, 640, 3)


def test_the_summary_and_the_banners_are_lines_one_below_the_other_with_no_gap() -> None:
    from trafficpi.core.geometry import Line
    from trafficpi.vision.rules import CountingLine

    image = picture()
    line = CountingLine("main", Line((0.5, 0.0), (0.5, 1.0)), "Main")
    draw(image, lines=[line], tracks=[], trails={}, banners=["a banner"], totals=CountTotals())
    for row in (0, 1):  # the summary of the counts, then the banner
        assert white_columns(image, 4 + BANNER_ROW * row, 4 + BANNER_ROW * (row + 1)).size > 0
    no_summary = with_banners("a banner")
    assert white_columns(no_summary, 4, 4 + BANNER_ROW).size > 0  # without lines, it is the first


def test_nothing_to_say_draws_nothing() -> None:
    assert (with_banners() == picture()).all()
    assert (with_banners("") == picture()).all()
