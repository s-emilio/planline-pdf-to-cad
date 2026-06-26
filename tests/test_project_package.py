from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pdf_plan_to_dwg.app.models import (
    CalibrationRecord,
    JobState,
    PageInfo,
    PlanRegion,
    RectModel,
    ScaleCandidate,
)
from pdf_plan_to_dwg.app.project_models import (
    ProjectInfo,
    ProjectManifest,
    ProjectPage,
    ProjectPlan,
    ProjectRect,
    SourcePdfInfo,
    job_from_manifest,
    manifest_from_job,
)
from pdf_plan_to_dwg.app.project_package import (
    ProjectPackageError,
    read_project_package,
    sha256_bytes,
    write_project_package,
)


def sample_manifest(source_pdf: bytes) -> ProjectManifest:
    now = datetime.now(timezone.utc).isoformat()
    return ProjectManifest(
        project=ProjectInfo(
            id="project-123",
            name="Ranch Renovation",
            created_at=now,
            updated_at=now,
            app_version="0.1.0",
        ),
        source_pdf=SourcePdfInfo(
            original_filename="ranch.pdf",
            sha256=sha256_bytes(source_pdf),
            size_bytes=len(source_pdf),
        ),
        pages=[
            ProjectPage(
                number=1,
                width=792,
                height=612,
                rotation=0,
                preview="previews/page-001.png",
            )
        ],
        plans=[
            ProjectPlan(
                id="first-floor",
                page=1,
                name="First Floor",
                crop=ProjectRect(x0=80, y0=80, x1=560, y1=430),
                units="in",
                units_per_pdf_point=1 / 6,
                scale_label='1/4" = 1\'-0"',
                scale_confirmed=True,
                remove_text=True,
                exclusion_masks=[
                    ProjectRect(x0=300, y0=180, x1=360, y1=240)
                ],
            )
        ],
    )


def test_project_package_round_trip(vector_pdf: Path, tmp_path: Path):
    source_pdf = vector_pdf.read_bytes()
    manifest = sample_manifest(source_pdf)
    destination = write_project_package(
        tmp_path / "Ranch Renovation",
        manifest,
        source_pdf,
        previews={"page-001.png": b"preview"},
        exports={"plan.svg": b"<svg/>"},
    )

    assert destination.suffix == ".planline"
    loaded = read_project_package(destination)
    assert loaded.manifest == manifest
    assert loaded.source_pdf == source_pdf
    assert loaded.previews == {"page-001.png": b"preview"}
    assert loaded.exports == {"plan.svg": b"<svg/>"}


def test_project_package_rejects_pdf_checksum_mismatch(vector_pdf: Path, tmp_path: Path):
    source_pdf = vector_pdf.read_bytes()
    manifest = sample_manifest(source_pdf)
    path = tmp_path / "tampered.planline"
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("manifest.json", manifest.model_dump_json())
        bundle.writestr("source.pdf", source_pdf + b"tampered")

    with pytest.raises(ProjectPackageError, match="size does not match"):
        read_project_package(path)


def test_project_package_rejects_unsafe_archive_path(vector_pdf: Path, tmp_path: Path):
    source_pdf = vector_pdf.read_bytes()
    manifest = sample_manifest(source_pdf)
    path = tmp_path / "unsafe.planline"
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("manifest.json", manifest.model_dump_json())
        bundle.writestr("source.pdf", source_pdf)
        bundle.writestr("../outside.txt", "nope")

    with pytest.raises(ProjectPackageError, match="Unsafe project package path"):
        read_project_package(path)


def test_project_package_rejects_future_manifest_version(
    vector_pdf: Path,
    tmp_path: Path,
):
    source_pdf = vector_pdf.read_bytes()
    raw = sample_manifest(source_pdf).model_dump(mode="json")
    raw["version"] = 99
    path = tmp_path / "future.planline"
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("manifest.json", json.dumps(raw))
        bundle.writestr("source.pdf", source_pdf)

    with pytest.raises(ProjectPackageError, match="Unsupported project manifest version"):
        read_project_package(path)


def test_project_package_migrates_v0_manifest(vector_pdf: Path, tmp_path: Path):
    source_pdf = vector_pdf.read_bytes()
    raw = sample_manifest(source_pdf).model_dump(mode="json")
    raw["version"] = 0
    raw["project"]["original_filename"] = raw["source_pdf"]["original_filename"]
    raw["project"]["source_sha256"] = raw["source_pdf"]["sha256"]
    raw["project"]["source_size_bytes"] = raw["source_pdf"]["size_bytes"]
    del raw["source_pdf"]
    raw["plans"][0]["page_index"] = raw["plans"][0].pop("page") - 1
    raw["plans"][0]["units_per_point"] = raw["plans"][0].pop(
        "units_per_pdf_point"
    )
    raw["plans"][0]["exclude_regions"] = raw["plans"][0].pop("exclusion_masks")
    path = tmp_path / "legacy.planline"
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("manifest.json", json.dumps(raw))
        bundle.writestr("source.pdf", source_pdf)

    loaded = read_project_package(path)
    assert loaded.manifest.version == 1
    assert loaded.manifest.plans[0].page == 1
    assert loaded.manifest.plans[0].units_per_pdf_point == pytest.approx(1 / 6)
    assert len(loaded.manifest.plans[0].exclusion_masks) == 1


def test_manifest_rejects_crop_outside_page(vector_pdf: Path):
    source_pdf = vector_pdf.read_bytes()
    manifest = sample_manifest(source_pdf).model_dump()
    manifest["plans"][0]["crop"]["x1"] = 900

    with pytest.raises(ValueError, match="crop lies outside"):
        ProjectManifest.model_validate(manifest)


def test_manifest_rejects_unknown_fields(vector_pdf: Path):
    source_pdf = vector_pdf.read_bytes()
    manifest = sample_manifest(source_pdf).model_dump()
    manifest["future_setting"] = True

    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        ProjectManifest.model_validate(manifest)


def test_manifest_from_job_preserves_calibration_and_detection(vector_pdf: Path):
    source_pdf = vector_pdf.read_bytes()
    state = JobState(
        id="job-123",
        filename="original-plan.pdf",
        status="ready",
        pages=[
            PageInfo(
                index=0,
                width=792,
                height=612,
                rotation=0,
                vector_paths=12,
                vector_items=34,
                image_coverage=0.1,
                raster_only=False,
                scale_candidates=[
                    ScaleCandidate(
                        label='1/4" = 1\'-0"',
                        units="in",
                        units_per_point=1 / 6,
                        confidence=0.9,
                        source="page text",
                    )
                ],
            )
        ],
        plans=[
            PlanRegion(
                id="first-floor",
                page_index=0,
                name="First Floor",
                crop=RectModel(x0=80, y0=80, x1=560, y1=430),
                confidence=0.8,
                units_per_point=1 / 6,
                scale_confirmed=True,
                calibration=CalibrationRecord(
                    x1=100,
                    y1=100,
                    x2=172,
                    y2=100,
                    distance=12,
                    units="in",
                ),
            )
        ],
    )

    manifest = manifest_from_job(
        state,
        project_name="Ranch Renovation",
        source_sha256=sha256_bytes(source_pdf),
        source_size_bytes=len(source_pdf),
    )

    assert manifest.project.id == state.id
    assert manifest.pages[0].scale_candidates[0].units_per_pdf_point == pytest.approx(
        1 / 6
    )
    assert manifest.plans[0].calibration is not None
    assert manifest.plans[0].calibration.points == ((100, 100), (172, 100))

    restored = job_from_manifest(manifest, project_id="imported-123")
    assert restored.id == "imported-123"
    assert restored.project_name == "Ranch Renovation"
    assert restored.pages[0].scale_candidates[0].units_per_point == pytest.approx(
        1 / 6
    )
    assert restored.plans[0].calibration is not None
    assert restored.plans[0].calibration.x1 == 100
    assert restored.plans[0].calibration.x2 == 172
