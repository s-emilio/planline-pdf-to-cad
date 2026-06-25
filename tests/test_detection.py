from __future__ import annotations

from pdf_plan_to_dwg.app.detector import analyze_document, drawable_paths


def test_detects_vector_plan_and_scale(vector_pdf):
    pages, plans, warnings = analyze_document(str(vector_pdf))
    assert not warnings
    assert pages[0].vector_items >= 20
    assert pages[0].scale_candidates[0].units == "in"
    assert plans
    assert plans[0].crop.x0 < 90
    assert plans[0].crop.x1 > 550


def test_rejects_raster_only_sheet(raster_pdf):
    pages, plans, warnings = analyze_document(str(raster_pdf))
    assert pages[0].raster_only
    assert plans == []
    assert any("raster-only" in warning for warning in warnings)


def test_ignores_extended_pdf_clip_records_without_rect():
    drawings = [
        {"type": "clip", "items": [("re",)], "scissor": (0, 0, 100, 100)},
        {"type": "group", "rect": (0, 0, 100, 100), "items": []},
        {"type": "s", "rect": (10, 10, 90, 90), "items": [("l",)]},
    ]
    assert drawable_paths(drawings) == [drawings[2]]
