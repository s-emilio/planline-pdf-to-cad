from __future__ import annotations

from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version as package_version
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import (
    CalibrationRecord,
    JobState,
    PageInfo,
    PlanRegion,
    RectModel,
    ScaleCandidate,
    utc_now,
)


PROJECT_FORMAT = "planline-project"
PROJECT_FORMAT_VERSION = 1
PROJECT_SOURCE_PATH = "source.pdf"


def current_app_version() -> str:
    try:
        return package_version("planline-pdf-to-cad")
    except PackageNotFoundError:
        return "0.1.0"


class StrictProjectModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ProjectInfo(StrictProjectModel):
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    created_at: str
    updated_at: str
    app_version: str
    source: Literal["source.pdf"] = PROJECT_SOURCE_PATH

    @field_validator("created_at", "updated_at")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Project timestamps must include a timezone.")
        return value


class SourcePdfInfo(StrictProjectModel):
    original_filename: str = Field(min_length=1, max_length=255)
    sha256: str
    size_bytes: int = Field(gt=0)

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        normalized = value.lower()
        if len(normalized) != 64 or any(
            character not in "0123456789abcdef" for character in normalized
        ):
            raise ValueError("PDF checksum must be a SHA-256 hex digest.")
        return normalized


class ProjectRect(StrictProjectModel):
    x0: float
    y0: float
    x1: float
    y1: float

    @model_validator(mode="after")
    def validate_size(self) -> "ProjectRect":
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError("Rectangle coordinates must be normalized and non-empty.")
        return self


class ProjectScaleCandidate(StrictProjectModel):
    label: str
    units: Literal["in", "mm"]
    units_per_pdf_point: float = Field(gt=0)
    confidence: float = Field(ge=0, le=1)
    source: str
    rect: ProjectRect | None = None


class ProjectPage(StrictProjectModel):
    number: int = Field(ge=1)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    rotation: int
    preview: str | None = None
    vector_paths: int = Field(default=0, ge=0)
    vector_items: int = Field(default=0, ge=0)
    image_coverage: float = Field(default=0, ge=0, le=1)
    raster_only: bool = False
    scale_candidates: list[ProjectScaleCandidate] = Field(default_factory=list)

    @field_validator("rotation")
    @classmethod
    def validate_rotation(cls, value: int) -> int:
        if value % 90:
            raise ValueError("Page rotation must be a multiple of 90 degrees.")
        return value % 360


class ProjectCalibration(StrictProjectModel):
    points: tuple[tuple[float, float], tuple[float, float]]
    distance: float = Field(gt=0)
    units: Literal["in", "ft", "mm", "cm", "m"]

    @model_validator(mode="after")
    def validate_points(self) -> "ProjectCalibration":
        if self.points[0] == self.points[1]:
            raise ValueError("Calibration points must be distinct.")
        return self


class ProjectPlan(StrictProjectModel):
    id: str = Field(min_length=1, max_length=128)
    page: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=200)
    crop: ProjectRect
    rotation: Literal[0, 90, 180, 270] = 0
    confidence: float = Field(default=0, ge=0, le=1)
    confirmed: bool = False
    scale_confirmed: bool = False
    units: Literal["in", "mm"] = "in"
    units_per_pdf_point: float | None = Field(default=None, gt=0)
    scale_label: str | None = None
    calibration: ProjectCalibration | None = None
    remove_text: bool = False
    orthogonal_only: bool = False
    angle_tolerance: float = Field(default=3.0, ge=0, le=15)
    exclusion_masks: list[ProjectRect] = Field(default_factory=list)
    detection_notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ExportPreferences(StrictProjectModel):
    format: Literal["all", "svg", "cad", "blend"] = "all"


class ProjectManifest(StrictProjectModel):
    format: Literal["planline-project"] = PROJECT_FORMAT
    version: Literal[1] = PROJECT_FORMAT_VERSION
    project: ProjectInfo
    source_pdf: SourcePdfInfo
    pages: list[ProjectPage]
    plans: list[ProjectPlan]
    export_preferences: ExportPreferences = Field(default_factory=ExportPreferences)
    markups: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_project_geometry(self) -> "ProjectManifest":
        page_by_number = {page.number: page for page in self.pages}
        if len(page_by_number) != len(self.pages):
            raise ValueError("Page numbers must be unique.")
        plan_ids: set[str] = set()
        for plan in self.plans:
            if plan.id in plan_ids:
                raise ValueError(f"Duplicate plan ID: {plan.id}")
            plan_ids.add(plan.id)
            page = page_by_number.get(plan.page)
            if page is None:
                raise ValueError(f"Plan {plan.id} references a missing page.")
            _validate_rect_bounds(plan.crop, page.width, page.height, f"Plan {plan.id} crop")
            for index, mask in enumerate(plan.exclusion_masks, 1):
                _validate_rect_bounds(
                    mask,
                    page.width,
                    page.height,
                    f"Plan {plan.id} exclusion mask {index}",
                )
                if (
                    mask.x0 < plan.crop.x0
                    or mask.y0 < plan.crop.y0
                    or mask.x1 > plan.crop.x1
                    or mask.y1 > plan.crop.y1
                ):
                    raise ValueError(
                        f"Plan {plan.id} exclusion mask {index} lies outside its crop."
                    )
            if plan.calibration:
                for point in plan.calibration.points:
                    if not (0 <= point[0] <= page.width and 0 <= point[1] <= page.height):
                        raise ValueError(
                            f"Plan {plan.id} calibration point lies outside its page."
                        )
        return self


def _validate_rect_bounds(
    rect: ProjectRect,
    width: float,
    height: float,
    label: str,
) -> None:
    if rect.x0 < 0 or rect.y0 < 0 or rect.x1 > width or rect.y1 > height:
        raise ValueError(f"{label} lies outside its page.")


def manifest_from_job(
    state: JobState,
    *,
    project_name: str,
    source_sha256: str,
    source_size_bytes: int,
) -> ProjectManifest:
    now = datetime.now(timezone.utc).isoformat()
    pages = [
        ProjectPage(
            number=page.index + 1,
            width=page.width,
            height=page.height,
            rotation=page.rotation,
            vector_paths=page.vector_paths,
            vector_items=page.vector_items,
            image_coverage=page.image_coverage,
            raster_only=page.raster_only,
            scale_candidates=[
                ProjectScaleCandidate(
                    label=candidate.label,
                    units=candidate.units,
                    units_per_pdf_point=candidate.units_per_point,
                    confidence=candidate.confidence,
                    source=candidate.source,
                    rect=(
                        ProjectRect(**candidate.rect.normalized().model_dump())
                        if candidate.rect
                        else None
                    ),
                )
                for candidate in page.scale_candidates
            ],
        )
        for page in state.pages
    ]
    plans = []
    for plan in state.plans:
        crop = plan.crop.normalized()
        calibration = None
        if plan.calibration:
            calibration = ProjectCalibration(
                points=(
                    (plan.calibration.x1, plan.calibration.y1),
                    (plan.calibration.x2, plan.calibration.y2),
                ),
                distance=plan.calibration.distance,
                units=plan.calibration.units,
            )
        plans.append(
            ProjectPlan(
                id=plan.id,
                page=plan.page_index + 1,
                name=plan.name,
                crop=ProjectRect(**crop.model_dump()),
                rotation=plan.rotation,
                confidence=plan.confidence,
                confirmed=plan.confirmed,
                scale_confirmed=plan.scale_confirmed,
                units=plan.units,
                units_per_pdf_point=plan.units_per_point,
                scale_label=plan.scale_label,
                calibration=calibration,
                remove_text=plan.remove_text,
                orthogonal_only=plan.orthogonal_only,
                angle_tolerance=plan.angle_tolerance,
                exclusion_masks=[
                    ProjectRect(**mask.normalized().model_dump())
                    for mask in plan.exclude_regions
                ],
                detection_notes=plan.detection_notes,
                warnings=plan.warnings,
            )
        )
    return ProjectManifest(
        project=ProjectInfo(
            id=state.id,
            name=project_name,
            created_at=state.created_at,
            updated_at=now,
            app_version=current_app_version(),
        ),
        source_pdf=SourcePdfInfo(
            original_filename=state.filename,
            sha256=source_sha256,
            size_bytes=source_size_bytes,
        ),
        pages=pages,
        plans=plans,
        export_preferences=ExportPreferences(format=state.export_format),
    )


def job_from_manifest(
    manifest: ProjectManifest,
    *,
    project_id: str,
) -> JobState:
    pages = [
        PageInfo(
            index=page.number - 1,
            width=page.width,
            height=page.height,
            rotation=page.rotation,
            vector_paths=page.vector_paths,
            vector_items=page.vector_items,
            image_coverage=page.image_coverage,
            raster_only=page.raster_only,
            scale_candidates=[
                ScaleCandidate(
                    label=candidate.label,
                    units=candidate.units,
                    units_per_point=candidate.units_per_pdf_point,
                    confidence=candidate.confidence,
                    source=candidate.source,
                    rect=(
                        RectModel(**candidate.rect.model_dump())
                        if candidate.rect
                        else None
                    ),
                )
                for candidate in page.scale_candidates
            ],
        )
        for page in manifest.pages
    ]
    plans = []
    for plan in manifest.plans:
        calibration = None
        if plan.calibration:
            calibration = CalibrationRecord(
                x1=plan.calibration.points[0][0],
                y1=plan.calibration.points[0][1],
                x2=plan.calibration.points[1][0],
                y2=plan.calibration.points[1][1],
                distance=plan.calibration.distance,
                units=plan.calibration.units,
            )
        plans.append(
            PlanRegion(
                id=plan.id,
                page_index=plan.page - 1,
                name=plan.name,
                crop=RectModel(**plan.crop.model_dump()),
                rotation=plan.rotation,
                confidence=plan.confidence,
                confirmed=plan.confirmed,
                scale_confirmed=plan.scale_confirmed,
                units=plan.units,
                units_per_point=plan.units_per_pdf_point,
                scale_label=plan.scale_label,
                calibration=calibration,
                remove_text=plan.remove_text,
                orthogonal_only=plan.orthogonal_only,
                angle_tolerance=plan.angle_tolerance,
                exclude_regions=[
                    RectModel(**mask.model_dump())
                    for mask in plan.exclusion_masks
                ],
                detection_notes=plan.detection_notes,
                warnings=plan.warnings,
            )
        )
    return JobState(
        id=project_id,
        filename=manifest.source_pdf.original_filename,
        project_name=manifest.project.name,
        created_at=manifest.project.created_at,
        updated_at=utc_now(),
        source_sha256=manifest.source_pdf.sha256,
        status="ready",
        pages=pages,
        plans=plans,
        export_format=manifest.export_preferences.format,
    )
