from __future__ import annotations

from pathlib import Path
from threading import Thread

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .jobs import (
    add_plan,
    cleanup_job,
    create_job,
    delete_plan,
    export_job,
    job_dir,
    load_state,
    render_page_preview,
    save_state,
    update_plan,
)
from .models import CalibrationRequest, ExportRequest, PlanCreate, PlanUpdate
from .scales import calibration_units_per_point


APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
MAX_UPLOAD_BYTES = 250 * 1024 * 1024

app = FastAPI(
    title="PDF Planset to Editable DWG",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url=None,
)


def _state(job_id: str):
    try:
        return load_state(job_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Job not found.") from None


@app.get("/api/health")
def health() -> dict:
    from .converter import find_oda_converter
    from .ocr import tesseract_available

    converter = find_oda_converter()
    return {
        "ok": True,
        "oda_available": converter is not None,
        "oda_path": str(converter) if converter else None,
        "ocr_available": tesseract_available(),
    }


@app.post("/api/jobs")
async def upload_job(file: UploadFile = File(...)):
    filename = file.filename or "planset.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Upload a PDF file.")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="PDF exceeds the 250 MB local limit.")
    try:
        state = create_job(filename, content)
        if state.status == "error":
            raise HTTPException(
                status_code=422,
                detail=f"PDF analysis failed: {state.error or 'unknown error'}",
            )
        return state
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    return _state(job_id)


@app.get("/api/jobs/{job_id}/pages/{page_index}/preview")
def page_preview(job_id: str, page_index: int):
    try:
        return FileResponse(render_page_preview(job_id, page_index), media_type="image/png")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Job not found.") from None
    except IndexError:
        raise HTTPException(status_code=404, detail="Page not found.") from None


@app.patch("/api/jobs/{job_id}/plans/{plan_id}")
def patch_plan(job_id: str, plan_id: str, change: PlanUpdate):
    state = _state(job_id)
    try:
        plan = update_plan(state, plan_id, **change.model_dump(exclude_unset=True))
    except KeyError:
        raise HTTPException(status_code=404, detail="Plan not found.") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return plan


@app.post("/api/jobs/{job_id}/plans")
def create_plan(job_id: str, request: PlanCreate):
    state = _state(job_id)
    try:
        return add_plan(state, request.page_index, request.crop, request.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/jobs/{job_id}/plans/{plan_id}/calibrate")
def calibrate_plan(job_id: str, plan_id: str, request: CalibrationRequest):
    state = _state(job_id)
    try:
        calibration = request.model_dump(
            include={"x1", "y1", "x2", "y2", "distance", "units"}
        )
        units, units_per_point = calibration_units_per_point(**calibration)
        plan_changes = {
            key: value
            for key, value in request.model_dump(
                include={"name", "crop", "rotation"}
            ).items()
            if value is not None
        }
        return update_plan(
            state,
            plan_id,
            **plan_changes,
            units=units,
            units_per_point=units_per_point,
            scale_label=f"Manual: {request.distance:g} {request.units}",
            scale_confirmed=True,
            confirmed=True,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Plan not found.") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/jobs/{job_id}/plans/{plan_id}", status_code=204)
def remove_plan(job_id: str, plan_id: str):
    state = _state(job_id)
    try:
        delete_plan(state, plan_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Plan not found.") from None


@app.post("/api/jobs/{job_id}/export")
def create_export(job_id: str):
    state = _state(job_id)
    try:
        archive = export_job(state)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Export failed: {exc}") from exc
    return FileResponse(
        archive,
        media_type="application/zip",
        filename=archive.name,
    )


def _run_export(job_id: str, export_format: str) -> None:
    try:
        export_job(load_state(job_id), export_format)
    except Exception:
        # export_job persists the useful error and progress event for the UI.
        pass


@app.post("/api/jobs/{job_id}/export/start", status_code=202)
def start_export(job_id: str, request: ExportRequest):
    state = _state(job_id)
    if state.status == "exporting":
        return {
            "status": state.status,
            "progress": state.export_progress,
            "step": state.export_step,
        }
    selected = [
        plan
        for plan in state.plans
        if plan.scale_confirmed and plan.units_per_point
    ]
    if not selected:
        raise HTTPException(
            status_code=400,
            detail="Calibrate or select a scale for at least one plan before export.",
        )
    incomplete = [
        plan.name
        for plan in selected
        if not plan.scale_confirmed or not plan.units_per_point
    ]
    if incomplete:
        raise HTTPException(
            status_code=400,
            detail="Confirm the scale for: " + ", ".join(incomplete),
        )
    state.status = "exporting"
    state.error = None
    state.export_progress = 0
    state.export_step = "Queued for export"
    state.export_events = ["Queued for export"]
    save_state(state)
    state.export_format = request.format
    save_state(state)
    Thread(target=_run_export, args=(job_id, request.format), daemon=True).start()
    return {"status": "exporting", "progress": 0, "step": "Queued for export"}


@app.get("/api/jobs/{job_id}/export/status")
def export_status(job_id: str):
    state = _state(job_id)
    return {
        "status": state.status,
        "progress": state.export_progress,
        "step": state.export_step,
        "events": state.export_events,
        "error": state.error,
        "download_ready": bool(state.status == "complete" and state.export_name),
    }


@app.get("/api/jobs/{job_id}/download")
def download_export(job_id: str):
    state = _state(job_id)
    if not state.export_name:
        raise HTTPException(status_code=404, detail="No export exists for this job.")
    path = job_dir(job_id) / state.export_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Export file is missing.")
    return FileResponse(path, media_type="application/zip", filename=path.name)


@app.delete("/api/jobs/{job_id}", status_code=204)
def delete_job(job_id: str):
    try:
        cleanup_job(job_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Job not found.") from None


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
