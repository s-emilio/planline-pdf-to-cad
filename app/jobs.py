from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path

import pymupdf

from .converter import (
    build_drawing_model,
    convert_svg_to_blend,
    convert_dxf_to_dwg,
    make_comparison,
    report_for_model,
    render_dxf,
    render_source_crop,
    write_dxf,
    write_reports,
    write_svg,
)
from .detector import analyze_document
from .models import JobState, PlanRegion, ProjectSummary, RectModel, utc_now
from .project_models import job_from_manifest, manifest_from_job
from .project_package import read_project_package, write_project_package


JOBS_ROOT = Path(
    os.environ.get("PLANLINE_PROJECTS_DIR", Path.home() / "Planline Projects")
).expanduser()


def ensure_jobs_root() -> Path:
    JOBS_ROOT.mkdir(parents=True, exist_ok=True)
    return JOBS_ROOT


def job_dir(job_id: str) -> Path:
    return ensure_jobs_root() / job_id


def state_path(job_id: str) -> Path:
    return job_dir(job_id) / "project.json"


def legacy_state_path(job_id: str) -> Path:
    return job_dir(job_id) / "job.json"


def pdf_path(job_id: str) -> Path:
    return job_dir(job_id) / "source.pdf"


def save_state(state: JobState) -> None:
    state.updated_at = utc_now()
    destination = state_path(state.id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(destination)


def _resolved_plan_warnings(plan: PlanRegion) -> list[str]:
    warnings = list(plan.warnings)
    if plan.scale_confirmed and plan.units_per_point:
        resolved = {
            "Manual crop and scale require confirmation.",
            "No drawing scale detected. Calibrate manually.",
            "Multiple drawing scales detected. Confirm the proposed scale or calibrate manually.",
        }
        warnings = [warning for warning in warnings if warning not in resolved]
    if plan.confirmed:
        warnings = [
            warning
            for warning in warnings
            if warning != "Low-confidence crop: review and confirm before export."
        ]
    return warnings


def load_state(job_id: str) -> JobState:
    path = state_path(job_id)
    if not path.is_file():
        path = legacy_state_path(job_id)
    if not path.is_file():
        raise FileNotFoundError(job_id)
    state = JobState.model_validate_json(path.read_text(encoding="utf-8"))
    changed = False
    if not state.project_name:
        state.project_name = Path(state.filename).stem
        changed = True
    source = pdf_path(job_id)
    if not state.source_sha256 and source.is_file():
        state.source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
        changed = True
    for plan in state.plans:
        if plan.scale_confirmed and plan.units_per_point and not plan.confirmed:
            plan.confirmed = True
            changed = True
        warnings = _resolved_plan_warnings(plan)
        if warnings != plan.warnings:
            plan.warnings = warnings
            changed = True
    if changed:
        save_state(state)
    return state


def create_job(
    filename: str,
    content: bytes,
    project_name: str | None = None,
) -> JobState:
    if not content.startswith(b"%PDF"):
        raise ValueError("The uploaded file is not a PDF.")
    job_id = uuid.uuid4().hex[:16]
    directory = job_dir(job_id)
    directory.mkdir(parents=True)
    pdf_path(job_id).write_bytes(content)
    clean_filename = Path(filename).name
    clean_project_name = (project_name or Path(clean_filename).stem).strip()
    if not clean_project_name:
        clean_project_name = "Untitled Project"
    state = JobState(
        id=job_id,
        filename=clean_filename,
        project_name=clean_project_name[:200],
        source_sha256=hashlib.sha256(content).hexdigest(),
    )
    save_state(state)
    try:
        pages, plans, warnings = analyze_document(str(pdf_path(job_id)))
        state.pages = pages
        state.plans = plans
        state.warnings = warnings
        state.status = "ready"
    except Exception as exc:
        state.status = "error"
        state.error = str(exc)
    save_state(state)
    return state


def update_plan(state: JobState, plan_id: str, **changes) -> PlanRegion:
    for index, plan in enumerate(state.plans):
        if plan.id != plan_id:
            continue
        values = plan.model_dump()
        requested_units = changes.get("units")
        if (
            requested_units
            and requested_units != plan.units
            and "units_per_point" not in changes
            and plan.units_per_point
        ):
            values["units_per_point"] = (
                plan.units_per_point * 25.4
                if requested_units == "mm"
                else plan.units_per_point / 25.4
            )
        for key, value in changes.items():
            if value is not None:
                values[key] = value
        updated = PlanRegion.model_validate(values)
        page = state.pages[updated.page_index]
        crop = updated.crop.normalized()
        updated.crop = RectModel(
            x0=max(0, min(page.width, crop.x0)),
            y0=max(0, min(page.height, crop.y0)),
            x1=max(0, min(page.width, crop.x1)),
            y1=max(0, min(page.height, crop.y1)),
        )
        if updated.crop.x1 - updated.crop.x0 < 2 or updated.crop.y1 - updated.crop.y0 < 2:
            raise ValueError("The crop region is too small.")
        normalized_masks: list[RectModel] = []
        for mask in updated.exclude_regions:
            mask = mask.normalized()
            clipped = RectModel(
                x0=max(updated.crop.x0, min(updated.crop.x1, mask.x0)),
                y0=max(updated.crop.y0, min(updated.crop.y1, mask.y0)),
                x1=max(updated.crop.x0, min(updated.crop.x1, mask.x1)),
                y1=max(updated.crop.y0, min(updated.crop.y1, mask.y1)),
            )
            if clipped.x1 - clipped.x0 >= 2 and clipped.y1 - clipped.y0 >= 2:
                normalized_masks.append(clipped)
        updated.exclude_regions = normalized_masks
        if updated.scale_confirmed and updated.units_per_point:
            updated.confirmed = True
        updated.warnings = _resolved_plan_warnings(updated)
        state.plans[index] = updated
        save_state(state)
        return updated
    raise KeyError(plan_id)


def add_plan(state: JobState, page_index: int, crop: RectModel, name: str | None) -> PlanRegion:
    if page_index >= len(state.pages):
        raise ValueError("Page does not exist.")
    plan_id = f"p{page_index + 1}-manual-{uuid.uuid4().hex[:6]}"
    plan = PlanRegion(
        id=plan_id,
        page_index=page_index,
        name=name or f"Page {page_index + 1} Manual Plan",
        crop=crop.normalized(),
        confidence=0,
        detection_notes=["manually added crop"],
        warnings=["Manual crop and scale require confirmation."],
    )
    state.plans.append(plan)
    save_state(state)
    return plan


def delete_plan(state: JobState, plan_id: str) -> None:
    original_count = len(state.plans)
    state.plans = [plan for plan in state.plans if plan.id != plan_id]
    if len(state.plans) == original_count:
        raise KeyError(plan_id)
    save_state(state)


def render_page_preview(job_id: str, page_index: int) -> Path:
    state = load_state(job_id)
    if page_index < 0 or page_index >= len(state.pages):
        raise IndexError(page_index)
    destination = job_dir(job_id) / "previews" / f"page-{page_index + 1:03d}.png"
    if destination.exists():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    document = pymupdf.open(pdf_path(job_id))
    try:
        page = document[page_index]
        matrix_scale = min(2.0, 1600 / max(page.rect.width, page.rect.height))
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(matrix_scale, matrix_scale), alpha=False)
        pixmap.save(destination)
    finally:
        document.close()
    return destination


def _safe_stem(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip("-")
    return stem[:80] or "plan"


def _record_export_event(state: JobState, progress: int, message: str) -> None:
    state.export_progress = max(0, min(100, progress))
    state.export_step = message
    if not state.export_events or state.export_events[-1] != message:
        state.export_events.append(message)
        state.export_events = state.export_events[-120:]
    save_state(state)


def export_job(state: JobState, export_format: str = "all") -> Path:
    if export_format not in {"all", "svg", "cad", "blend"}:
        raise ValueError("Unsupported export format.")
    selected = [
        plan
        for plan in state.plans
        if plan.scale_confirmed and plan.units_per_point
    ]
    if not selected:
        raise ValueError("Calibrate or select a scale for at least one plan before export.")
    incomplete = [plan.name for plan in selected if not plan.scale_confirmed or not plan.units_per_point]
    if incomplete:
        raise ValueError("Confirm the scale for: " + ", ".join(incomplete))

    state.status = "exporting"
    state.error = None
    state.export_name = None
    state.export_progress = 0
    state.export_step = "Starting export"
    state.export_events = []
    state.export_format = export_format
    _record_export_event(state, 1, "Starting vector export")
    output_dir = job_dir(state.id) / "exports"
    shutil.rmtree(output_dir, ignore_errors=True)
    output_dir.mkdir(parents=True)
    reports: list[dict] = []
    export_warnings = [
        warning
        for warning in state.warnings
        if "ODA File Converter" not in warning and "Blender" not in warning
    ]
    try:
        for index, plan in enumerate(selected, 1):
            plan_start = 5 + round((index - 1) * 78 / len(selected))
            plan_end = 5 + round(index * 78 / len(selected))
            _record_export_event(
                state,
                plan_start,
                f"Plan {index}/{len(selected)}: preparing {plan.name}",
            )
            stem = f"{index:02d}-{_safe_stem(plan.name)}"
            dxf_path = output_dir / f"{stem}.dxf" if export_format in {"all", "cad"} else None
            svg_path = output_dir / f"{stem}.svg" if export_format in {"all", "svg", "blend"} else None
            blend_path = output_dir / f"{stem}.blend" if export_format == "blend" else None
            vector_progress = plan_start + max(1, round((plan_end - plan_start) * 0.55))

            def conversion_event(message: str) -> None:
                event_progress = vector_progress
                count_match = re.search(r"([\d,]+)/([\d,]+)", message)
                if count_match:
                    current = int(count_match.group(1).replace(",", ""))
                    total = max(1, int(count_match.group(2).replace(",", "")))
                    vector_begin = plan_start + 3
                    event_progress = vector_begin + round(
                        (vector_progress - vector_begin) * min(1, current / total)
                    )
                _record_export_event(
                    state,
                    event_progress,
                    f"{plan.name}: {message}",
                )

            model = build_drawing_model(
                pdf_path(state.id),
                plan,
                progress=conversion_event,
            )
            dxf_counts = None
            if svg_path:
                _record_export_event(
                    state,
                    plan_start + round((plan_end - plan_start) * 0.59),
                    f"{plan.name}: writing editable SVG",
                )
                write_svg(model, svg_path)
                _record_export_event(
                    state,
                    plan_start + round((plan_end - plan_start) * 0.63),
                    f"{plan.name}: SVG written",
                )
            if dxf_path:
                _record_export_event(
                    state,
                    plan_start + round((plan_end - plan_start) * 0.66),
                    f"{plan.name}: writing DXF from shared drawing model",
                )
                dxf_counts = write_dxf(model, dxf_path)
                _record_export_event(
                    state,
                    plan_start + round((plan_end - plan_start) * 0.7),
                    f"{plan.name}: DXF written",
                )
            report = report_for_model(model, dxf_path, svg_path, dxf_counts)
            report["plan_id"] = plan.id
            report["blend"] = None
            report["blend_mesh"] = None
            source_preview = output_dir / f"{stem}-source.png"
            cad_preview = output_dir / f"{stem}-cad.png"
            comparison = output_dir / f"{stem}-comparison.png"
            _record_export_event(
                state,
                plan_start + round((plan_end - plan_start) * 0.68),
                f"{plan.name}: rendering PDF crop",
            )
            render_source_crop(pdf_path(state.id), plan, source_preview)
            if blend_path and svg_path:
                _record_export_event(
                    state,
                    plan_start + round((plan_end - plan_start) * 0.7),
                    f"{plan.name}: importing SVG into Blender as Grease Pencil",
                )
                converted, blend_warning, blend_stats = convert_svg_to_blend(
                    svg_path,
                    source_preview,
                    blend_path,
                    model,
                )
                if converted:
                    report["blend"] = converted.name
                    report["blend_mesh"] = blend_stats
                    _record_export_event(
                        state,
                        plan_start + round((plan_end - plan_start) * 0.8),
                        f"{plan.name}: Blender edge mesh written",
                    )
                if blend_warning:
                    report["warnings"].append(blend_warning)
                    export_warnings.append(f"{plan.name}: {blend_warning}")
            if dxf_path:
                try:
                    _record_export_event(
                        state,
                        plan_start + round((plan_end - plan_start) * 0.76),
                        f"{plan.name}: rendering CAD preview",
                    )
                    render_dxf(
                        dxf_path,
                        cad_preview,
                    )
                    make_comparison(source_preview, cad_preview, comparison)
                except Exception as exc:
                    preview_warning = (
                        f"CAD preview could not be rendered, but the DXF is valid: {exc}"
                    )
                    report["warnings"].append(preview_warning)
                    export_warnings.append(f"{plan.name}: {preview_warning}")
                    _record_export_event(
                        state,
                        plan_start + round((plan_end - plan_start) * 0.8),
                        f"{plan.name}: preview skipped; keeping DXF",
                    )
                _record_export_event(
                    state,
                    plan_start + round((plan_end - plan_start) * 0.86),
                    f"{plan.name}: checking DWG converter",
                )
                dwg_path, dwg_warning = convert_dxf_to_dwg(dxf_path, output_dir)
                if dwg_path:
                    report["dwg"] = dwg_path.name
                if dwg_warning:
                    report["warnings"].append(dwg_warning)
                    export_warnings.append(f"{plan.name}: {dwg_warning}")
            elif export_format == "svg":
                _record_export_event(
                    state,
                    plan_start + round((plan_end - plan_start) * 0.82),
                    f"{plan.name}: SVG-only export; CAD conversion skipped",
                )
            reports.append(report)
            _record_export_event(state, plan_end, f"{plan.name}: plan export complete")

        _record_export_event(state, 88, "Writing conversion reports")
        write_reports(output_dir, state.filename, reports, sorted(set(export_warnings)))
        _record_export_event(state, 94, "Packaging vector files and previews")
        archive = job_dir(state.id) / f"{_safe_stem(Path(state.filename).stem)}-vector-export.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for path in output_dir.iterdir():
                bundle.write(path, arcname=path.name)
        state.status = "complete"
        state.export_name = archive.name
        state.warnings = sorted(set(export_warnings))
        _record_export_event(state, 100, "Export complete — download is ready")
        return archive
    except Exception as exc:
        state.status = "error"
        state.error = str(exc)
        _record_export_event(state, state.export_progress, f"Export failed: {exc}")
        raise


def cleanup_job(job_id: str) -> None:
    directory = job_dir(job_id)
    if not directory.exists():
        raise FileNotFoundError(job_id)
    shutil.rmtree(directory)


def project_summary(state: JobState) -> ProjectSummary:
    return ProjectSummary(
        id=state.id,
        name=state.project_name or Path(state.filename).stem,
        filename=state.filename,
        created_at=state.created_at,
        updated_at=state.updated_at,
        archived=state.archived,
        page_count=len(state.pages),
        plan_count=len(state.plans),
        status=state.status,
    )


def list_projects(*, include_archived: bool = False) -> list[ProjectSummary]:
    projects: list[ProjectSummary] = []
    for directory in ensure_jobs_root().iterdir():
        if not directory.is_dir():
            continue
        try:
            state = load_state(directory.name)
        except (FileNotFoundError, ValueError):
            continue
        if state.archived and not include_archived:
            continue
        projects.append(project_summary(state))
    return sorted(projects, key=lambda project: project.updated_at, reverse=True)


def update_project(
    state: JobState,
    *,
    name: str | None = None,
    archived: bool | None = None,
) -> JobState:
    if name is not None:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Project name cannot be empty.")
        state.project_name = clean_name[:200]
    if archived is not None:
        state.archived = archived
    save_state(state)
    return state


def duplicate_project(state: JobState, name: str | None = None) -> JobState:
    new_id = uuid.uuid4().hex[:16]
    destination = job_dir(new_id)
    shutil.copytree(job_dir(state.id), destination)
    duplicate = state.model_copy(deep=True)
    duplicate.id = new_id
    duplicate.project_name = (
        name.strip() if name and name.strip() else f"{project_summary(state).name} Copy"
    )[:200]
    duplicate.created_at = utc_now()
    duplicate.updated_at = duplicate.created_at
    duplicate.archived = False
    duplicate.status = "ready"
    duplicate.error = None
    duplicate.export_name = None
    duplicate.export_progress = 0
    duplicate.export_step = None
    duplicate.export_events = []
    shutil.rmtree(destination / "exports", ignore_errors=True)
    for generated in destination.glob("*.zip"):
        generated.unlink()
    legacy = destination / "job.json"
    legacy.unlink(missing_ok=True)
    save_state(duplicate)
    return duplicate


def import_project_package(content: bytes) -> JobState:
    temporary_path: Path | None = None
    project_id = uuid.uuid4().hex[:16]
    destination = job_dir(project_id)
    try:
        with tempfile.NamedTemporaryFile(suffix=".planline", delete=False) as temporary:
            temporary.write(content)
            temporary_path = Path(temporary.name)
        loaded = read_project_package(temporary_path)
        state = job_from_manifest(loaded.manifest, project_id=project_id)
        destination.mkdir(parents=True)
        pdf_path(project_id).write_bytes(loaded.source_pdf)
        _write_imported_files(destination / "previews", loaded.previews)
        _write_imported_files(destination / "exports", loaded.exports)
        save_state(state)
        return state
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    finally:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)


def package_project(state: JobState) -> Path:
    source = pdf_path(state.id)
    source_content = source.read_bytes()
    checksum = hashlib.sha256(source_content).hexdigest()
    state.source_sha256 = checksum
    manifest = manifest_from_job(
        state,
        project_name=project_summary(state).name,
        source_sha256=checksum,
        source_size_bytes=len(source_content),
    )
    previews = _directory_files(job_dir(state.id) / "previews")
    exports = _directory_files(job_dir(state.id) / "exports")
    destination = job_dir(state.id) / f"{_safe_stem(project_summary(state).name)}.planline"
    path = write_project_package(
        destination,
        manifest,
        source_content,
        previews=previews,
        exports=exports,
    )
    save_state(state)
    return path


def _directory_files(directory: Path) -> dict[str, Path]:
    if not directory.is_dir():
        return {}
    return {
        path.relative_to(directory).as_posix(): path
        for path in directory.rglob("*")
        if path.is_file()
    }


def _write_imported_files(directory: Path, files: dict[str, bytes]) -> None:
    for name, content in files.items():
        destination = directory / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
