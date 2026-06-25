from __future__ import annotations

import pytest

from pdf_plan_to_dwg.app.scales import (
    calibration_units_per_point,
    parse_scale_candidates,
)


def test_parses_imperial_architectural_scale():
    candidates = parse_scale_candidates('FLOOR PLAN SCALE: 1/4" = 1\'-0"')
    assert len(candidates) == 1
    assert candidates[0].units == "in"
    assert candidates[0].units_per_point == pytest.approx(48 / 72)


def test_parses_metric_ratio():
    candidate = parse_scale_candidates("Scale 1:100")[0]
    assert candidate.units == "mm"
    assert candidate.units_per_point == pytest.approx(100 * 25.4 / 72)


def test_manual_calibration_normalizes_output_units():
    units, factor = calibration_units_per_point(0, 0, 72, 0, 10, "ft")
    assert units == "in"
    assert factor == pytest.approx(120 / 72)


def test_manual_calibration_rejects_identical_points():
    with pytest.raises(ValueError):
        calibration_units_per_point(10, 10, 10, 10, 5, "m")

