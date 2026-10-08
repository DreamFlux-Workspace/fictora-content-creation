"""Colour-match a take to the board the human approved (one curve for the whole take).

The board is what the creator said yes to; a take can come back in another
look. One transform per take, fitted in CIE Lab, moves it toward the board:

- lightness: quantile mapping from the take's L to the board's L, with a floor
  so a very dark board never sinks the take below 10% mean luma;
- colour: a/b moved about the grade's cast (the least coloured quarter of the
  mid-tones), with one spread factor clamped to 0.35-1.8; the change fades out
  for near-black so shadows do not tint.

The board's gutters and border (full-length flat rows/columns) are left out of
the sample. The transform is written as a 33-point ``.cube`` and applied with
ffmpeg ``lut3d``; audio is copied. Output is a new versioned file.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import numpy.typing as npt
from PIL import Image

from creation.post.media import MediaToolError, ffmpeg_bin, probe_video, run_ffmpeg

Float = npt.NDArray[np.float64]

QUANTILES = (0.5, 1.0, *[float(q) for q in range(5, 100, 5)], 99.0, 99.5)
CHROMA_SCALE_BOUNDS = (0.35, 1.8)
NEUTRAL_SHARE = 0.25
NEUTRAL_L = (20.0, 97.0)
DARK_FADE_L = (2.0, 15.0)
DARK_FLOOR_LUMA = 0.10
LUT_SIZE = 33
SAMPLE_WIDTH = 96
SAMPLE_FPS = 2.0
BOARD_WIDTH = 512
_FLAT_SHARE = 0.92
_FLAT_TOLERANCE = 10.0

_WHITE = np.array([0.95047, 1.0, 1.08883])
_RGB_TO_XYZ = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ]
)
_XYZ_TO_RGB = np.linalg.inv(_RGB_TO_XYZ)
_EPS = 216 / 24389
_KAPPA = 24389 / 27


def srgb_to_lab(rgb: Float) -> Float:
    """sRGB 0..1 -> CIE Lab (D65)."""

    rgb = np.clip(np.asarray(rgb, dtype=np.float64), 0.0, 1.0)
    linear = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    xyz = linear @ _RGB_TO_XYZ.T / _WHITE
    f = np.where(xyz > _EPS, np.cbrt(xyz), (_KAPPA * xyz + 16.0) / 116.0)
    lab = np.empty_like(f)
    lab[..., 0] = 116.0 * f[..., 1] - 16.0
    lab[..., 1] = 500.0 * (f[..., 0] - f[..., 1])
    lab[..., 2] = 200.0 * (f[..., 1] - f[..., 2])
    return lab


def lab_to_srgb(lab: Float) -> Float:
    """CIE Lab (D65) -> sRGB 0..1, clipped."""

    lab = np.asarray(lab, dtype=np.float64)
    fy = (lab[..., 0] + 16.0) / 116.0
    f = np.stack([fy + lab[..., 1] / 500.0, fy, fy - lab[..., 2] / 200.0], axis=-1)
    cubed = f**3
    xyz = np.where(cubed > _EPS, cubed, (116.0 * f - 16.0) / _KAPPA)
    xyz[..., 1] = np.where(
        lab[..., 0] > _KAPPA * _EPS, cubed[..., 1], lab[..., 0] / _KAPPA
    )
    linear = np.clip((xyz * _WHITE) @ _XYZ_TO_RGB.T, 0.0, 1.0)
    rgb = np.where(
        linear <= 0.0031308, linear * 12.92, 1.055 * np.power(linear, 1 / 2.4) - 0.055
    )
    return np.asarray(np.clip(rgb, 0.0, 1.0), dtype=np.float64)


def _smoothstep(values: Float, low: float, high: float) -> Float:
    t = np.clip((values - low) / (high - low), 0.0, 1.0)
    return np.asarray(t * t * (3.0 - 2.0 * t), dtype=np.float64)


def _increasing(values: list[float]) -> list[float]:
    out: list[float] = []
    for value in values:
        out.append(value if not out or value > out[-1] else out[-1] + 1e-4)
    return out


def _neutral_ab(lab: Float) -> Float:
    lightness = lab[:, 0]
    mid = (lightness > NEUTRAL_L[0]) & (lightness < NEUTRAL_L[1])
    pool = lab[mid] if mid.sum() >= 50 else lab
    chroma = np.hypot(pool[:, 1], pool[:, 2])
    neutral = pool[chroma <= np.percentile(chroma, NEUTRAL_SHARE * 100.0)]
    return np.asarray(neutral[:, 1:].mean(axis=0), dtype=np.float64)


def _spread(lab: Float, centre: Float) -> float:
    offset = lab[:, 1:] - centre
    return float(np.sqrt((offset**2).sum(axis=1).mean()))


def _luma(rgb: Float) -> float:
    return float((rgb @ np.array([0.2126, 0.7152, 0.0722])).mean())


@dataclass(frozen=True)
class ColourTransform:
    """One transform for a whole take."""

    l_from: tuple[float, ...]
    l_to: tuple[float, ...]
    take_ab: tuple[float, float]
    board_ab: tuple[float, float]
    chroma_scale: float
    strength: float = 1.0
    lightness_share: float = 1.0

    def apply_rgb(self, rgb: Float) -> Float:
        """Apply to sRGB 0..1 ``(..., 3)``."""

        lab = srgb_to_lab(rgb)
        out = lab.copy()
        lightness = lab[..., 0]
        mapped = np.interp(lightness, self.l_from, self.l_to)
        out[..., 0] = lightness + self.strength * self.lightness_share * (
            mapped - lightness
        )
        weight = self.strength * _smoothstep(lightness, *DARK_FADE_L)
        for axis in (1, 2):
            target = self.board_ab[axis - 1] + self.chroma_scale * (
                lab[..., axis] - self.take_ab[axis - 1]
            )
            out[..., axis] = lab[..., axis] + weight * (target - lab[..., axis])
        return lab_to_srgb(out)


def fit_transform(
    take_pixels: Float, board_pixels: Float, *, strength: float = 1.0
) -> ColourTransform:
    """Fit the transform that moves the take's pixels toward the board's.

    Parameters
    ----------
    take_pixels, board_pixels
        ``(N, 3)`` sRGB 0..1.
    strength
        0..1.

    Returns
    -------
    ColourTransform
        The transform.

    Raises
    ------
    ValueError
        When either side is empty or ``strength`` is outside 0..1.
    """

    if not 0.0 <= strength <= 1.0:
        raise ValueError(f"colour strength must be 0..1, got {strength}")
    take_rgb = np.asarray(take_pixels, dtype=np.float64).reshape(-1, 3)
    board_rgb = np.asarray(board_pixels, dtype=np.float64).reshape(-1, 3)
    if not len(take_rgb) or not len(board_rgb):
        raise ValueError("colour match needs pixels from both the take and the board")
    take_lab, board_lab = srgb_to_lab(take_rgb), srgb_to_lab(board_rgb)
    take_ab, board_ab = _neutral_ab(take_lab), _neutral_ab(board_lab)
    take_spread = _spread(take_lab, take_ab)
    scale = _spread(board_lab, board_ab) / take_spread if take_spread > 1e-6 else 1.0
    transform = ColourTransform(
        l_from=tuple(
            _increasing(
                [0.0, *np.percentile(take_lab[:, 0], QUANTILES).tolist(), 100.0]
            )
        ),
        l_to=tuple(
            _increasing(
                [0.0, *np.percentile(board_lab[:, 0], QUANTILES).tolist(), 100.0]
            )
        ),
        take_ab=(float(take_ab[0]), float(take_ab[1])),
        board_ab=(float(board_ab[0]), float(board_ab[1])),
        chroma_scale=float(
            min(max(scale, CHROMA_SCALE_BOUNDS[0]), CHROMA_SCALE_BOUNDS[1])
        ),
        strength=strength,
    )
    sample = take_rgb[:: max(1, len(take_rgb) // 20_000)]
    floor = min(DARK_FLOOR_LUMA, _luma(sample))
    if _luma(transform.apply_rgb(sample)) >= floor:
        return transform
    low, high = 0.0, 1.0
    for _ in range(12):
        mid = (low + high) / 2
        if _luma(replace(transform, lightness_share=mid).apply_rgb(sample)) >= floor:
            low = mid
        else:
            high = mid
    return replace(transform, lightness_share=low)


def _flat_lines(grey: Float) -> npt.NDArray[np.bool_]:
    """Rows that are gutters: a thin flat run, or a flat run touching the edge (a border)."""

    median = np.median(grey, axis=1, keepdims=True)
    flat = (np.abs(grey - median) <= _FLAT_TOLERANCE).mean(axis=1) >= _FLAT_SHARE
    size = len(flat)
    thin = max(2, round(size * 0.03))
    gutters = np.zeros(size, dtype=bool)
    start = 0
    while start < size:
        if not flat[start]:
            start += 1
            continue
        end = start
        while end < size and flat[end]:
            end += 1
        if end - start <= thin or (
            (start == 0 or end == size) and end - start <= size * 0.1
        ):
            gutters[start:end] = True
        start = end
    return gutters


def _widen(lines: npt.NDArray[np.bool_], radius: int) -> npt.NDArray[np.bool_]:
    if not lines.any():
        return lines
    return (
        np.convolve(lines.astype(np.float64), np.ones(2 * radius + 1), mode="same") > 0
    )


def board_pixels(board: Path) -> Float:
    """The board's picture-cell pixels (gutters and border left out), 0..1."""

    with Image.open(board) as opened:
        image = opened.convert("RGB")
        height = max(2, round(image.height * BOARD_WIDTH / image.width))
        array = np.asarray(image.resize((BOARD_WIDTH, height)), dtype=np.float64)
    grey = array.mean(axis=2)
    rows = _widen(_flat_lines(grey), max(1, round(grey.shape[0] * 0.012)))
    cols = _widen(_flat_lines(grey.T), max(1, round(grey.shape[1] * 0.012)))
    mask = ~rows[:, None] & ~cols[None, :]
    if mask.mean() < 0.2:
        mask = np.zeros(grey.shape, dtype=bool)
        dy, dx = (
            max(1, round(grey.shape[0] * 0.03)),
            max(1, round(grey.shape[1] * 0.03)),
        )
        mask[dy : grey.shape[0] - dy, dx : grey.shape[1] - dx] = True
    return array[mask] / 255.0


def take_pixels(take: Path) -> Float:
    """Frames across the take at 2 fps, 96 px wide, pooled, 0..1."""

    info = probe_video(take)
    height = max(2, 2 * round(info.height * SAMPLE_WIDTH / info.width / 2))
    result = subprocess.run(
        [ffmpeg_bin(), "-v", "error", "-i", str(take), "-vf", f"fps={SAMPLE_FPS},scale={SAMPLE_WIDTH}:{height}",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdin=subprocess.DEVNULL, capture_output=True, check=False,
    )  # fmt: skip
    if result.returncode != 0 or not result.stdout:
        raise MediaToolError(f"could not sample frames from {take.name}")
    return (
        np.frombuffer(result.stdout, dtype=np.uint8).reshape(-1, 3).astype(np.float64)
        / 255.0
    )


def write_cube(transform: ColourTransform, path: Path, *, size: int = LUT_SIZE) -> Path:
    """Write the transform as a ``.cube`` 3D LUT (red fastest) for ffmpeg ``lut3d``."""

    axis = np.linspace(0.0, 1.0, size)
    blue, green, red = np.meshgrid(axis, axis, axis, indexing="ij")
    mapped = transform.apply_rgb(np.stack([red, green, blue], axis=-1).reshape(-1, 3))
    lines = [
        'TITLE "board colour match"',
        f"LUT_3D_SIZE {size}",
        "DOMAIN_MIN 0 0 0",
        "DOMAIN_MAX 1 1 1",
    ]
    lines += [f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in mapped]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@dataclass(frozen=True)
class ColourResult:
    """What the colour match wrote, and the look before and after (mean luma, 0..1)."""

    output: Path
    lut: Path
    board_luma: float
    before_luma: float
    after_luma: float

    def one_line(self) -> str:
        """Operator line."""

        return (
            f"board luma {self.board_luma:.2f} | take {self.before_luma:.2f} -> {self.after_luma:.2f} "
            f"(LUT {self.lut.name})"
        )


def colour_match(
    take: Path, board: Path, out: Path, *, strength: float = 1.0
) -> ColourResult:
    """Match ``take`` to ``board`` and write ``out`` (plus its ``.cube``).

    Raises
    ------
    FileExistsError
        When ``out`` exists.
    FileNotFoundError
        When the take or board is missing.
    """

    for path in (take, board):
        if not path.is_file():
            raise FileNotFoundError(f"not found: {path}")
    if out.exists():
        raise FileExistsError(f"{out} exists; local post never overwrites")
    board_px = board_pixels(board)
    before = take_pixels(take)
    transform = fit_transform(before, board_px, strength=strength)
    lut = write_cube(transform, out.with_suffix(".cube"))
    escaped = str(lut).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    args = [
        "-i",
        str(take),
        "-vf",
        f"lut3d=file='{escaped}':interp=tetrahedral",
        "-map",
        "0:v:0",
    ]
    if probe_video(take).has_audio:
        args += ["-map", "0:a:0", "-c:a", "copy"]
    run_ffmpeg(
        [
            *args,
            "-c:v",
            "libx264",
            "-crf",
            "16",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(out),
        ]
    )
    after = take_pixels(out)
    return ColourResult(out, lut, _luma(board_px), _luma(before), _luma(after))
