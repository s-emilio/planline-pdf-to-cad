from __future__ import annotations

import pytest

from pdf_plan_to_dwg.app.geometry import (
    CadTransform,
    clip_segment_excluding,
    dedupe_segments,
    join_collinear_segments,
)
from pdf_plan_to_dwg.app.models import RectModel


def test_pdf_coordinates_become_bottom_left_cad_coordinates():
    transform = CadTransform(RectModel(x0=10, y0=20, x1=110, y1=220), 2, 0)
    assert transform.point((10, 220)) == pytest.approx((0, 0))
    assert transform.point((110, 20)) == pytest.approx((200, 400))


def test_rotation_keeps_extents_positive():
    transform = CadTransform(RectModel(x0=0, y0=0, x1=100, y1=200), 1, 90)
    points = [
        transform.point((0, 0)),
        transform.point((100, 0)),
        transform.point((100, 200)),
        transform.point((0, 200)),
    ]
    assert min(point[0] for point in points) == pytest.approx(0)
    assert min(point[1] for point in points) == pytest.approx(0)
    assert transform.width == 200
    assert transform.height == 100


def test_deduplicates_and_joins_collinear_segments():
    segments = [
        ((0, 0), (10, 0)),
        ((10, 0), (20, 0)),
        ((10, 0), (0, 0)),
    ]
    unique = dedupe_segments(segments, 0.001)
    chains = join_collinear_segments(unique, 0.001)
    assert len(unique) == 2
    assert len(chains) == 1
    assert chains[0] in [[(0, 0), (10, 0), (20, 0)], [(20, 0), (10, 0), (0, 0)]]


def test_exclusion_mask_splits_crossing_segment():
    crop = RectModel(x0=0, y0=0, x1=100, y1=100)
    mask = RectModel(x0=40, y0=20, x1=60, y1=80)
    segments = clip_segment_excluding((0, 50), (100, 50), crop, [mask])
    assert segments == [((0.0, 50.0), (40.0, 50.0)), ((60.0, 50.0), (100.0, 50.0))]
