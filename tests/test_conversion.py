from __future__ import annotations

import json
import zipfile
import xml.etree.ElementTree as ET

import ezdxf
import pytest

from pdf_plan_to_dwg.app import jobs
from pdf_plan_to_dwg.app.converter import convert_plan_to_dxf, write_svg
from pdf_plan_to_dwg.app.drawing_model import build_drawing_model
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


def test_changing_units_preserves_physical_scale(vector_pdf, tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "JOBS_ROOT", tmp_path / "jobs")
    state = jobs.create_job("sample.pdf", vector_pdf.read_bytes())
    original = state.plans[0].units_per_point
    updated = jobs.update_plan(state, state.plans[0].id, units="mm")
    assert updated.units_per_point == pytest.approx(original * 25.4)
