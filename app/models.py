from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RectModel(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float

    def normalized(self) -> "RectModel":
        return RectModel(
            x0=min(self.x0, self.x1),
            y0=min(self.y0, self.y1),
            x1=max(self.x0, self.x1),
            y1=max(self.y0, self.y1),
        )


class ScaleCandidate(BaseModel):
    label: str
    units: Literal["in", "mm"]
    units_per_point: float
    confidence: float = Field(ge=0, le=1)
    source: str
    rect: RectModel | None = None


class PlanRegion(BaseModel):
    id: str
    page_index: int
    name: str
    crop: RectModel
    rotation: Literal[0, 90, 180, 270] = 0
    confidence: float = Field(ge=0, le=1)
    confirmed: bool = False
    scale_confirmed: bool = False
    units: Literal["in", "mm"] = "in"
    units_per_point: float | None = None
    scale_label: str | None = None
    remove_text: bool = False
    orthogonal_only: bool = False
    angle_tolerance: float = Field(default=3.0, ge=0, le=15)
    exclude_regions: list[RectModel] = Field(default_factory=list)
    detection_notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PageInfo(BaseModel):
    index: int
    width: float
    height: float
    rotation: int
    vector_paths: int
    vector_items: int
    image_coverage: float
    raster_only: bool
    scale_candidates: list[ScaleCandidate] = Field(default_factory=list)


class JobState(BaseModel):
    id: str
    filename: str
    created_at: str = Field(default_factory=utc_now)
    status: Literal["analyzing", "ready", "exporting", "complete", "error"] = "analyzing"
    pages: list[PageInfo] = Field(default_factory=list)
    plans: list[PlanRegion] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    export_name: str | None = None
    export_progress: int = Field(default=0, ge=0, le=100)
    export_step: str | None = None
    export_events: list[str] = Field(default_factory=list)
    export_format: Literal["all", "svg", "cad", "blend"] = "all"


class PlanUpdate(BaseModel):
    name: str | None = None
    crop: RectModel | None = None
    rotation: Literal[0, 90, 180, 270] | None = None
    units: Literal["in", "mm"] | None = None
    units_per_point: float | None = Field(default=None, gt=0)
    scale_label: str | None = None
    confirmed: bool | None = None
    scale_confirmed: bool | None = None
    remove_text: bool | None = None
    orthogonal_only: bool | None = None
    angle_tolerance: float | None = Field(default=None, ge=0, le=15)
    exclude_regions: list[RectModel] | None = None


class PlanCreate(BaseModel):
    page_index: int = Field(ge=0)
    crop: RectModel
    name: str | None = None


class CalibrationRequest(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float
    distance: float = Field(gt=0)
    units: Literal["in", "ft", "mm", "cm", "m"]
    name: str | None = None
    crop: RectModel | None = None
    rotation: Literal[0, 90, 180, 270] | None = None


class ExportRequest(BaseModel):
    format: Literal["all", "svg", "cad", "blend"] = "all"
