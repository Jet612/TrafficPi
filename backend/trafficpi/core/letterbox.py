"""Aspect-preserving resize-and-pad of an image into a model's square input."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

PAD_VALUE = 114  # the grey the YOLO family is trained with


@dataclass(frozen=True, slots=True)
class Letterbox:
    """Fit a source image inside a ``dst_w`` x ``dst_h`` canvas without distorting it.

    The image is scaled to fit and centred; the rest of the canvas is padding. Boxes the model
    returns (fractions of the canvas) map back to fractions of the source image with
    :meth:`box_to_source`.
    """

    src_w: int
    src_h: int
    dst_w: int
    dst_h: int
    new_w: int  # size of the scaled image inside the canvas
    new_h: int
    pad_x: int  # left padding, pixels
    pad_y: int  # top padding, pixels

    @classmethod
    def fit(cls, src_w: int, src_h: int, dst_w: int, dst_h: int) -> Letterbox:
        if min(src_w, src_h, dst_w, dst_h) <= 0:
            raise ValueError("all sizes must be positive")
        scale = min(dst_w / src_w, dst_h / src_h)
        new_w = min(dst_w, max(1, round(src_w * scale)))
        new_h = min(dst_h, max(1, round(src_h * scale)))
        return cls(
            src_w, src_h, dst_w, dst_h, new_w, new_h, (dst_w - new_w) // 2, (dst_h - new_h) // 2
        )

    def new_canvas(self) -> np.ndarray:
        """A canvas filled with the padding colour. Reuse it across frames with ``out=``."""
        return np.full((self.dst_h, self.dst_w, 3), PAD_VALUE, dtype=np.uint8)

    def apply(self, image: np.ndarray, out: np.ndarray | None = None) -> np.ndarray:
        """Scale ``image`` into the canvas.

        With ``out`` (from :meth:`new_canvas`) only the image region is written, so the padding
        stays intact and no per-frame allocation or fill is needed.
        """
        if image.shape[:2] != (self.src_h, self.src_w):
            raise ValueError(
                f"expected a {self.src_w}x{self.src_h} image, got {image.shape[1]}x{image.shape[0]}"
            )
        canvas = self.new_canvas() if out is None else out
        if (self.new_w, self.new_h) == (self.src_w, self.src_h):
            scaled = image
        else:
            scaled = cv2.resize(image, (self.new_w, self.new_h), interpolation=cv2.INTER_AREA)
        canvas[self.pad_y : self.pad_y + self.new_h, self.pad_x : self.pad_x + self.new_w] = scaled
        return canvas

    def box_to_source(
        self, x0: float, y0: float, x1: float, y1: float
    ) -> tuple[float, float, float, float]:
        """Map a box (fractions of the canvas) to fractions of the source image, clipped to 0-1."""

        def fx(v: float) -> float:
            return min(1.0, max(0.0, (v * self.dst_w - self.pad_x) / self.new_w))

        def fy(v: float) -> float:
            return min(1.0, max(0.0, (v * self.dst_h - self.pad_y) / self.new_h))

        return fx(x0), fy(y0), fx(x1), fy(y1)
