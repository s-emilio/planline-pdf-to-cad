from __future__ import annotations

import re
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path

import pymupdf

from .converter import (
    build_drawing_model,
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
from .models import JobState, PlanRegion, RectModel


JOBS_ROOT = Path(tempfile.gettempdir()) / "pdf-plan-to-dwg-jobs"


def ensure_jobs_root() -> Path:
    JOBS_ROOT.mkdir(parents=True, exist_ok=True)
    return JOBS_ROOT


def job_dir(job_id: str) -> Path:
    return ensure_jobs_root() / job_id


def state_path(job_id: str) -> Path:
    return job_dir(job_id) / "job.json"


def pdf_path(job_id: str) -> Path:
    return job_dir(job_id) / "source.pdf"


def save_state(state: JobState) -> None:
    destination = state_path(state.id)
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
        raise FileNotFoundError(job_id)
    state = JobState.model_validate_json(path.read_text(encoding="utf-8"))
    changed = False
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


def create_job(filename: str, content: bytes) -> JobState:
    if not content.startswith(b"%PDF"):
        raise ValueError("The uploaded file is not a PDF.")
    job_id = uuid.uuid4().hex[:16]
    directory = job_dir(job_id)
    directory.mkdir(parents=True)
    pdf_path(job_id).write_bytes(content)
    state = JobState(id=job_id, filename=Path(filename).name)
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
    destination = job_dir(job_id) / f"page-{page_index + 1}.png"
    if destination.exists():
        return destination
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
    if export_format not in {"all", "svg", "cad"}:
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
    output_dir = job_dir(state.id) / "export"
    shutil.rmtree(output_dir, ignore_errors=True)
    output_dir.mkdir(parents=True)
    reports: list[dict] = []
    export_warnings = list(state.warnings)
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
            svg_path = output_dir / f"{stem}.svg" if export_format in {"all", "svg"} else None
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
            source_preview = output_dir / f"{stem}-source.png"
            cad_preview = output_dir / f"{stem}-cad.png"
            comparison = output_dir / f"{stem}-comparison.png"
            _record_export_event(
                state,
                plan_start + round((plan_end - plan_start) * 0.68),
                f"{plan.name}: rendering PDF crop",
            )
            render_source_crop(pdf_path(state.id), plan, source_preview)
            if dxf_path:
                try:
                    _record_export_event(
                        state,
                        plan_start + round((plan_end - plan_start) * 0.76),
                        f"{plan.name}: rendering CAD preview",
                    )
                    render_dxf(dxf_path, cad_preview)
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
            else:
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
