from __future__ import annotations

import json
import zipfile
import xml.etree.ElementTree as ET

import ezdxf
import pymupdf
import pytest
from PIL import Image

from pdf_plan_to_dwg.app import jobs
from pdf_plan_to_dwg.app.converter import (
    convert_plan_to_dxf,
    make_comparison,
    render_dxf,
    render_source_crop,
    write_dxf,
    write_svg,
)
from pdf_plan_to_dwg.app.drawing_model import (
    _is_orthogonal_segment,
    build_drawing_model,
)
from pdf_plan_to_dwg.app.geometry import CadTransform
from pdf_plan_to_dwg.app.models import PlanRegion, RectModel


def confirmed_plan() -> PlanRegion:
    return PlanRegion(
        id="plan-1",
        page_index=0,
        name="First Floor",
        crop=RectModel(x0=68, y0=68, x1=572, y1=490),
        confidence=0.8,
        confirmed=True,
        scale_confirmed=True,
        units="in",
        units_per_point=48 / 72,
        scale_label='1/4" = 1\'-0"',
    )


def test_exports_editable_dxf_entities(vector_pdf, tmp_path):
    path = tmp_path / "plan.dxf"
    report = convert_plan_to_dxf(vector_pdf, confirmed_plan(), path)
    document = ezdxf.readfile(path)
    types = [entity.dxftype() for entity in document.modelspace()]
    assert "LINE" in types
    assert "SPLINE" in types
    assert "HATCH" in types
    assert "TEXT" in types
    assert document.units == 1
    assert report["drawing_extents"]["width"] == pytest.approx(336)


def test_exports_editable_svg_from_shared_model(vector_pdf, tmp_path):
    model = build_drawing_model(vector_pdf, confirmed_plan())
    path = tmp_path / "plan.svg"
    write_svg(model, path)
    root = ET.parse(path).getroot()
    namespace = {"svg": "http://www.w3.org/2000/svg"}
    assert root.attrib["viewBox"] == "0 0 336 281.333333"
    assert root.attrib["width"] == "336in"
    assert root.findall(".//svg:path", namespace)
    assert root.findall(".//svg:polygon", namespace)
    assert root.findall(".//svg:text", namespace)
    assert "units_per_pdf_point" in root.find("svg:metadata", namespace).text


def test_shared_model_accepts_null_optional_pdf_style_values(vector_pdf, monkeypatch):
    import pymupdf

    original = pymupdf.Page.get_drawings

    def drawings_with_null_styles(page, extended=False):
        drawings = original(page, extended=extended)
        drawings[0]["width"] = None
        drawings[0]["stroke_opacity"] = None
        return drawings

    monkeypatch.setattr(pymupdf.Page, "get_drawings", drawings_with_null_styles)
    model = build_drawing_model(vector_pdf, confirmed_plan())
    assert model.polylines


def test_blender_cleanup_removes_text_curves_fills_and_diagonal_lines(vector_pdf):
    plan = confirmed_plan()
    unfiltered = build_drawing_model(vector_pdf, plan)
    plan.remove_text = True
    plan.orthogonal_only = True
    plan.angle_tolerance = 1
    filtered = build_drawing_model(vector_pdf, plan)

    assert unfiltered.texts
    assert unfiltered.curves
    assert unfiltered.polygons
    assert filtered.texts == []
    assert filtered.curves == []
    assert filtered.polygons == []
    assert filtered.unsupported["filtered_curves"] > 0
    for polyline in filtered.polylines:
        for start, end in zip(polyline.points, polyline.points[1:]):
            dx = abs(end[0] - start[0])
            dy = abs(end[1] - start[1])
            assert dx == pytest.approx(0, abs=1e-6) or dy == pytest.approx(0, abs=1e-6)


def test_angle_filter_keeps_near_orthogonal_segments_only():
    assert _is_orthogonal_segment((0, 0), (100, 1), 1)
    assert _is_orthogonal_segment((0, 0), (1, 100), 1)
    assert not _is_orthogonal_segment((0, 0), (100, 100), 5)


def test_filter_settings_are_written_to_svg_metadata(vector_pdf, tmp_path):
    plan = confirmed_plan()
    plan.remove_text = True
    plan.orthogonal_only = True
    plan.angle_tolerance = 5
    model = build_drawing_model(vector_pdf, plan)
    path = tmp_path / "filtered.svg"
    write_svg(model, path)
    root = ET.parse(path).getroot()
    namespace = {"svg": "http://www.w3.org/2000/svg"}
    metadata = json.loads(root.find("svg:metadata", namespace).text)
    assert metadata["filters"] == {
        "remove_text": True,
        "orthogonal_only": True,
        "angle_tolerance_degrees": 5,
        "exclude_regions": [],
    }


def test_comparison_previews_apply_quarter_turn_rotations(vector_pdf, tmp_path):
    plan = confirmed_plan()
    plan.rotation = 90
    source_path = tmp_path / "source.png"
    render_source_crop(vector_pdf, plan, source_path)
    with Image.open(source_path) as source:
        assert source.height > source.width

    model = build_drawing_model(vector_pdf, confirmed_plan())
    dxf_path = tmp_path / "plan.dxf"
    write_dxf(model, dxf_path)
    cad_path = tmp_path / "cad.png"
    render_dxf(dxf_path, cad_path, preview_rotation=90)
    with Image.open(cad_path) as cad:
        assert cad.height > cad.width


def test_comparison_has_twenty_pixel_image_margins(tmp_path):
    source_path = tmp_path / "source.png"
    cad_path = tmp_path / "cad.png"
    output_path = tmp_path / "comparison.png"
    Image.new("RGB", (100, 500), "red").save(source_path)
    Image.new("RGB", (200, 500), "blue").save(cad_path)

    make_comparison(source_path, cad_path, output_path)

    with Image.open(output_path) as comparison:
        assert comparison.size == (380, 616)
        assert comparison.getpixel((19, 96)) == (255, 255, 255)
        assert comparison.getpixel((20, 96)) == (255, 0, 0)
        assert comparison.getpixel((119, 595)) == (255, 0, 0)
        assert comparison.getpixel((120, 595)) == (255, 255, 255)
        assert comparison.getpixel((159, 96)) == (255, 255, 255)
        assert comparison.getpixel((160, 96)) == (0, 0, 255)
        assert comparison.getpixel((359, 595)) == (0, 0, 255)
        assert comparison.getpixel((360, 595)) == (255, 255, 255)


def test_exclusion_mask_trims_exported_geometry(vector_pdf):
    plan = confirmed_plan()
    plan.exclude_regions = [
        RectModel(x0=200, y0=70, x1=300, y1=440),
    ]
    model = build_drawing_model(vector_pdf, plan)

    assert model.exclude_regions == plan.exclude_regions
    assert model.unsupported["filtered_exclusion_segments"] > 0
    mask = plan.exclude_regions[0]
    transform = CadTransform(plan.crop, plan.units_per_point, plan.rotation)
    transformed_corners = [
        transform.point((mask.x0, mask.y0)),
        transform.point((mask.x1, mask.y1)),
    ]
    x0 = min(point[0] for point in transformed_corners)
    x1 = max(point[0] for point in transformed_corners)
    y0 = min(point[1] for point in transformed_corners)
    y1 = max(point[1] for point in transformed_corners)
    for polyline in model.polylines:
        for start, end in zip(polyline.points, polyline.points[1:]):
            midpoint = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
            assert not (x0 < midpoint[0] < x1 and y0 < midpoint[1] < y1)


def test_exclusion_mask_uses_visible_coordinates_on_rotated_page(tmp_path):
    pdf_path = tmp_path / "rotated-plan.pdf"
    document = pymupdf.open()
    page = document.new_page(width=100, height=200)
    page.draw_line((10, 100), (90, 100), width=1)
    page.set_rotation(270)
    document.save(pdf_path)
    document.close()

    plan = PlanRegion(
        id="rotated-plan",
        page_index=0,
        name="Rotated plan",
        crop=RectModel(x0=0, y0=0, x1=200, y1=100),
        confidence=1,
        confirmed=True,
        scale_confirmed=True,
        units="in",
        units_per_point=1,
        scale_label="1:1",
        exclude_regions=[RectModel(x0=90, y0=40, x1=110, y1=60)],
    )
    model = build_drawing_model(pdf_path, plan)

    assert model.width == pytest.approx(200)
    assert model.height == pytest.approx(100)
    vertical_segments = [
        (start, end)
        for polyline in model.polylines
        for start, end in zip(polyline.points, polyline.points[1:])
        if start[0] == pytest.approx(100) and end[0] == pytest.approx(100)
    ]
    assert len(vertical_segments) == 2
    y_ranges = sorted(
        (min(start[1], end[1]), max(start[1], end[1]))
        for start, end in vertical_segments
    )
    assert y_ranges == pytest.approx([(10, 40), (60, 90)])


def test_full_job_export_keeps_dxf_when_oda_is_missing(vector_pdf, tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "JOBS_ROOT", tmp_path / "jobs")
    state = jobs.create_job("sample.pdf", vector_pdf.read_bytes())
    assert state.plans
    plan = state.plans[0]
    plan.crop = RectModel(x0=68, y0=68, x1=572, y1=490)
    plan.confirmed = True
    plan.scale_confirmed = True
    state.plans[0] = plan
    jobs.save_state(state)
    archive = jobs.export_job(state)
    assert archive.is_file()
    completed = jobs.load_state(state.id)
    assert completed.export_progress == 100
    assert completed.export_step == "Export complete — download is ready"
    assert any("writing editable SVG" in event for event in completed.export_events)
    assert any("writing DXF" in event for event in completed.export_events)
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        assert any(name.endswith(".svg") for name in names)
        assert any(name.endswith(".dxf") for name in names)
        assert any(name.endswith("-comparison.png") for name in names)
        report = json.loads(bundle.read("conversion-report.json"))
        assert report["plans"][0]["entity_counts"]["LINE"] > 0
        assert report["plans"][0]["svg"].endswith(".svg")


def test_svg_only_export_omits_cad_files(vector_pdf, tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "JOBS_ROOT", tmp_path / "jobs")
    state = jobs.create_job("sample.pdf", vector_pdf.read_bytes())
    plan = state.plans[0]
    plan.crop = RectModel(x0=68, y0=68, x1=572, y1=490)
    plan.confirmed = True
    plan.scale_confirmed = True
    state.plans[0] = plan
    jobs.save_state(state)
    archive = jobs.export_job(state, "svg")
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        assert any(name.endswith(".svg") for name in names)
        assert not any(name.endswith(".dxf") for name in names)
        assert not any(name.endswith(".dwg") for name in names)
        report = json.loads(bundle.read("conversion-report.json"))
        assert report["plans"][0]["svg"].endswith(".svg")
        assert report["plans"][0]["dxf"] is None


def test_blend_export_packages_scaled_edge_mesh_metadata(
    vector_pdf, tmp_path, monkeypatch
):
    monkeypatch.setattr(jobs, "JOBS_ROOT", tmp_path / "jobs")

    def fake_blender(svg_path, source_png, output_path, model):
        assert source_png.is_file()
        output_path.write_bytes(b"BLENDER")
        return output_path, None, {
            "blender_version": "test",
            "object": "Plan_Edges",
            "vertices": 20,
            "edges": 16,
            "faces": 0,
            "width_m": model.width * 0.0254,
            "height_m": model.height * 0.0254,
            "x_rotation_applied_degrees": 180,
            "source_plane": "PDF_Source_Plane",
            "source_plane_faces": 1,
            "source_image_packed": True,
            "source_plane_alignment_x_m": 0.1,
            "source_plane_alignment_y_m": 0,
            "source_plane_rotation_x_degrees": 0,
            "source_plane_x_rotation_applied_degrees": 180,
        }

    monkeypatch.setattr(jobs, "convert_svg_to_blend", fake_blender)
    state = jobs.create_job("sample.pdf", vector_pdf.read_bytes())
    plan = state.plans[0]
    plan.crop = RectModel(x0=68, y0=68, x1=572, y1=490)
    plan.confirmed = True
    plan.scale_confirmed = True
    state.plans[0] = plan
    jobs.save_state(state)

    archive = jobs.export_job(state, "blend")

    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        assert any(name.endswith(".svg") for name in names)
        assert any(name.endswith(".blend") for name in names)
        assert not any(name.endswith(".dxf") for name in names)
        report = json.loads(bundle.read("conversion-report.json"))
        plan_report = report["plans"][0]
        assert plan_report["blend"].endswith(".blend")
        assert plan_report["blend_mesh"]["object"] == "Plan_Edges"
        assert plan_report["blend_mesh"]["faces"] == 0
        assert plan_report["blend_mesh"]["x_rotation_applied_degrees"] == 180
        assert plan_report["blend_mesh"]["source_plane"] == "PDF_Source_Plane"
        assert plan_report["blend_mesh"]["source_image_packed"] is True
        assert plan_report["blend_mesh"]["source_plane_rotation_x_degrees"] == 0
        assert (
            plan_report["blend_mesh"]["source_plane_x_rotation_applied_degrees"]
            == 180
        )


def test_changing_units_preserves_physical_scale(vector_pdf, tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "JOBS_ROOT", tmp_path / "jobs")
    state = jobs.create_job("sample.pdf", vector_pdf.read_bytes())
    original = state.plans[0].units_per_point
    updated = jobs.update_plan(state, state.plans[0].id, units="mm")
    assert updated.units_per_point == pytest.approx(original * 25.4)
