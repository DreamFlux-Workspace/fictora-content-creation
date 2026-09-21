"""Board luma helper tests."""

from __future__ import annotations

from io import BytesIO

from PIL import Image

from creation.ops.luma import measure_board_luma_bytes


def test_measure_mid_gray_board() -> None:
    """Mid gray lands near 50% mean luma."""

    image = Image.new("RGB", (8, 8), (128, 128, 128))
    buf = BytesIO()
    image.save(buf, format="PNG")
    report = measure_board_luma_bytes(buf.getvalue())
    assert 45.0 <= report.mean_percent <= 55.0
    assert not report.below_dim_floor
