"""Local board luma helper (deviation from product exposure API when needed)."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image

INTERIOR_DIM_BELOW_PERCENT = 25.0
INTERIOR_TARGET_MIN_PERCENT = 28.0
INTERIOR_TARGET_MAX_PERCENT = 35.0


def pixel_luma(pixel: tuple[int, int, int]) -> float:
    """Return Rec. 709 luma for one RGB pixel."""

    red, green, blue = pixel
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


@dataclass(frozen=True)
class BoardLumaReport:
    """Mean Rec. 709 luma for one storyboard image."""

    path: Path | None
    mean_percent: float
    below_dim_floor: bool
    in_interior_band: bool

    def one_line(self) -> str:
        """Return the operator-facing brightness line."""

        band = "yes" if self.in_interior_band else "no"
        dim = "yes" if self.below_dim_floor else "no"
        return f"brightness {self.mean_percent:.1f}%  interior_band={band}  dim_risk={dim}"


def _rgb_pixel(sample: object, *, label: Path | str) -> tuple[int, int, int]:
    if isinstance(sample, tuple) and len(sample) >= 3:
        red, green, blue = sample[0], sample[1], sample[2]
        if isinstance(red, int) and isinstance(green, int) and isinstance(blue, int):
            return red, green, blue
    raise ValueError(f"board image is not RGB: {label}")


def measure_board_luma(path: Path) -> BoardLumaReport:
    """Measure mean Rec. 709 luma of a storyboard PNG or JPEG."""

    if not path.is_file():
        raise FileNotFoundError(f"board image not found: {path}")
    return measure_board_luma_bytes(path.read_bytes(), path=path)


def measure_board_luma_bytes(data: bytes, *, path: Path | None = None) -> BoardLumaReport:
    """Measure mean Rec. 709 luma of storyboard image bytes."""

    label = path if path is not None else "in-memory board"
    try:
        with Image.open(BytesIO(data)) as image:
            rgb = image.convert("RGB")
            raw_pixels = list(rgb.get_flattened_data())
    except OSError as exc:
        raise ValueError(f"unreadable board image: {label}") from exc
    if not raw_pixels:
        raise ValueError(f"board image has no pixels: {label}")
    pixels = [_rgb_pixel(sample, label=label) for sample in raw_pixels]
    total = sum(pixel_luma(pixel) for pixel in pixels)
    mean = total / len(pixels)
    percent = 100.0 * mean / 255.0
    return BoardLumaReport(
        path=path,
        mean_percent=percent,
        below_dim_floor=percent <= INTERIOR_DIM_BELOW_PERCENT,
        in_interior_band=INTERIOR_TARGET_MIN_PERCENT <= percent <= INTERIOR_TARGET_MAX_PERCENT,
    )


__all__ = [
    "INTERIOR_DIM_BELOW_PERCENT",
    "INTERIOR_TARGET_MAX_PERCENT",
    "INTERIOR_TARGET_MIN_PERCENT",
    "BoardLumaReport",
    "measure_board_luma",
    "measure_board_luma_bytes",
]
