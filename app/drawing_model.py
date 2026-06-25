from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pymupdf

from .geometry import (
    CadTransform,
    clip_segment,
    clip_segment_excluding,
    color_hex,
    dedupe_segments,
    flatten_cubic,
    join_collinear_segments,
    safe_layer_name,
)
from .models import PlanRegion, RectModel
from .ocr import ocr_lines, tesseract_available


Point = tuple[float, float]
ProgressCallback = Callable[[str], None]


def _number_or(value: Any, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class StrokeStyle:
    color: str = "#000000"
    width: float = 0.0
    opacity: float = 1.0
    dasharray: tuple[float, ...] = ()
    linecap: str = "butt"
    linejoin: str = "miter"


@dataclass
class Polyline:
    layer: str
    points: list[Point]
    style: StrokeStyle


@dataclass
class CubicBezier:
    layer: str
    points: tuple[Point, Point, Point, Point]
    style: StrokeStyle


@dataclass
class Polygon:
    layer: str
    points: list[Point]
    fill: str
    opacity: float


@dataclass
class DrawingText:
    layer: str
    text: str
    insert: Point
    height: float
    rotation: float
    color: str
    opacity: float = 1.0
    font_family: str = "sans-serif"


@dataclass
class DrawingModel:
    name: str
    source_page: int
    crop: RectModel
    rotation: int
    units: str
    units_per_point: float
    scale_label: str | None
    confidence: float
    width: float
    height: float
    remove_text: bool = False
    orthogonal_only: bool = False
    angle_tolerance: float = 3.0
    exclude_regions: list[RectModel] = field(default_factory=list)
    polylines: list[Polyline] = field(default_factory=list)
    curves: list[CubicBezier] = field(default_factory=list)
    polygons: list[Polygon] = field(default_factory=list)
    texts: list[DrawingText] = field(default_factory=list)
    unsupported: Counter[str] = field(default_factory=Counter)
    warnings: list[str] = field(default_factory=list)

    @property
    def entity_counts(self) -> dict[str, int]:
        return {
            "POLYLINE": len(self.polylines),
            "CUBIC_BEZIER": len(self.curves),
            "POLYGON": len(self.polygons),
            "TEXT": len(self.texts),
        }

    @property
    def layer_counts(self) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for entity in (*self.polylines, *self.curves, *self.polygons, *self.texts):
            counts[entity.layer] += 1
        return dict(counts)


def _point(value: Any) -> Point:
    return float(value.x), float(value.y)


def _inside(point: Point, crop: RectModel, tolerance: float = 0.01) -> bool:
    return (
        crop.x0 - tolerance <= point[0] <= crop.x1 + tolerance
        and crop.y0 - tolerance <= point[1] <= crop.y1 + tolerance
    )


def _rect_touches_crop(rect: pymupdf.Rect, crop: RectModel) -> bool:
    return not (
        rect.x1 < crop.x0
        or rect.x0 > crop.x1
        or rect.y1 < crop.y0
        or rect.y0 > crop.y1
    )


def _rect_touches_any(rect: pymupdf.Rect, regions: list[RectModel]) -> bool:
    return any(
        not (
            rect.x1 <= region.x0
            or rect.x0 >= region.x1
            or rect.y1 <= region.y0
            or rect.y0 >= region.y1
        )
        for region in regions
    )


def _point_in_any(point: Point, regions: list[RectModel]) -> bool:
    return any(_inside(point, region, 0) for region in regions)


def _expanded_rect(rect: RectModel, amount: float, bounds: RectModel) -> RectModel:
    return RectModel(
        x0=max(bounds.x0, rect.x0 - amount),
        y0=max(bounds.y0, rect.y0 - amount),
        x1=min(bounds.x1, rect.x1 + amount),
        y1=min(bounds.y1, rect.y1 + amount),
    )


def _rect_is_text_geometry(
    rect: pymupdf.Rect,
    text_regions: list[RectModel],
    item_count: int,
) -> bool:
    if rect.width <= 0 or rect.height <= 0:
        return False
    center_x = (rect.x0 + rect.x1) / 2
    center_y = (rect.y0 + rect.y1) / 2
    for region in text_regions:
        if not (
            region.x0 <= center_x <= region.x1
            and region.y0 <= center_y <= region.y1
        ):
            continue
        if rect.width <= max(region.x1 - region.x0, 1) * 1.2 and rect.height <= max(
            region.y1 - region.y0, 1
        ) * 1.8:
            return True
    short_side = min(rect.width, rect.height)
    long_side = max(rect.width, rect.height)
    if 0.4 <= short_side <= 14 and long_side <= 220 and item_count >= 12:
        return True
    return False


def _segment_is_text_geometry(
    start: Point,
    end: Point,
    text_regions: list[RectModel],
) -> bool:
    center_x = (start[0] + end[0]) / 2
    center_y = (start[1] + end[1]) / 2
    length = math.dist(start, end)
    for region in text_regions:
        if not (
            region.x0 <= center_x <= region.x1
            and region.y0 <= center_y <= region.y1
        ):
            continue
        region_size = max(region.x1 - region.x0, region.y1 - region.y0, 1)
        if length <= region_size * 1.5:
            return True
    return False


def _is_orthogonal_segment(
    start: Point,
    end: Point,
    tolerance_degrees: float,
) -> bool:
    dx = abs(end[0] - start[0])
    dy = abs(end[1] - start[1])
    if dx <= 1e-9 and dy <= 1e-9:
        return False
    angle = math.degrees(math.atan2(dy, dx)) % 90
    distance = min(angle, 90 - angle)
    return distance <= tolerance_degrees


def _layer_for_path(path: dict, fill: bool = False) -> str:
    source_layer = safe_layer_name(path.get("layer", ""), "")
    if source_layer:
        return source_layer
    if fill:
        return safe_layer_name(f"PDF-FILL-{color_hex(path.get('fill'))}")
    width = round(_number_or(path.get("width"), 0), 2)
    return safe_layer_name(f"PDF-LINE-{color_hex(path.get('color'))}-W{width:g}")


def _rect_points(rect: Any) -> list[Point]:
    return [
        (float(rect.x0), float(rect.y0)),
        (float(rect.x1), float(rect.y0)),
        (float(rect.x1), float(rect.y1)),
        (float(rect.x0), float(rect.y1)),
    ]


def _quad_points(quad: Any) -> list[Point]:
    return [_point(quad.ul), _point(quad.ur), _point(quad.lr), _point(quad.ll)]


def _path_parts(path: dict) -> tuple[list[tuple[Point, Point]], list[tuple[Point, ...]], list[list[Point]], int]:
    segments: list[tuple[Point, Point]] = []
    curves: list[tuple[Point, ...]] = []
    closed_rings: list[list[Point]] = []
    unsupported = 0
    sequential: list[Point] = []
    for item in path.get("items", []):
        kind = item[0]
        if kind == "l":
            start, end = _point(item[1]), _point(item[2])
            segments.append((start, end))
            if not sequential:
                sequential.append(start)
            if math.dist(sequential[-1], start) <= 0.05:
                sequential.append(end)
            else:
                sequential = [start, end]
        elif kind == "c":
            curves.append(tuple(_point(value) for value in item[1:5]))
            sequential = []
        elif kind == "re":
            ring = _rect_points(item[1])
            closed_rings.append(ring)
            segments.extend(
                (ring[index], ring[(index + 1) % len(ring)])
                for index in range(len(ring))
            )
            sequential = []
        elif kind == "qu":
            ring = _quad_points(item[1])
            closed_rings.append(ring)
            segments.extend(
                (ring[index], ring[(index + 1) % len(ring)])
                for index in range(len(ring))
            )
            sequential = []
        else:
            unsupported += 1
            sequential = []
    if path.get("closePath") and len(sequential) >= 3:
        closed_rings.append(sequential)
        segments.append((sequential[-1], sequential[0]))
    return segments, curves, closed_rings, unsupported


def _color(value: tuple | None) -> str:
    return f"#{color_hex(value)}"


def _dasharray(value: str, scale: float) -> tuple[float, ...]:
    match = re.search(r"\[([^\]]*)\]", value or "")
    if not match:
        return ()
    numbers = []
    for token in match.group(1).split():
        try:
            number = abs(float(token)) * scale
        except ValueError:
            continue
        if number > 1e-9:
            numbers.append(number)
    return tuple(numbers)


def _stroke_style(path: dict, scale: float) -> StrokeStyle:
    line_caps = {0: "butt", 1: "round", 2: "square"}
    line_joins = {0: "miter", 1: "round", 2: "bevel"}
    caps = path.get("lineCap", (0,))
    cap = caps[0] if isinstance(caps, (tuple, list)) else caps
    return StrokeStyle(
        color=_color(path.get("color")),
        width=max(_number_or(path.get("width"), 0) * scale, scale * 0.01),
        opacity=_number_or(path.get("stroke_opacity"), 1),
        dasharray=_dasharray(path.get("dashes", ""), scale),
        linecap=line_caps.get(int(cap or 0), "butt"),
        linejoin=line_joins.get(int(path.get("lineJoin", 0) or 0), "miter"),
    )


def _text_rotation(transform: CadTransform, direction: tuple[float, float]) -> float:
    origin = transform.point((0, 0))
    transformed = transform.point(direction)
    return math.degrees(
        math.atan2(transformed[1] - origin[1], transformed[0] - origin[0])
    ) % 360


def _text_color(color: int) -> str:
    return f"#{color & 0xFFFFFF:06X}"


def unrotated_page_rect(page: pymupdf.Page, rect: RectModel) -> RectModel:
    transformed = pymupdf.Rect(
        rect.x0,
        rect.y0,
        rect.x1,
        rect.y1,
    ) * page.derotation_matrix
    return RectModel(
        x0=min(transformed.x0, transformed.x1),
        y0=min(transformed.y0, transformed.y1),
        x1=max(transformed.x0, transformed.x1),
        y1=max(transformed.y0, transformed.y1),
    )


def build_drawing_model(
    pdf_path: Path,
    plan: PlanRegion,
    progress: ProgressCallback | None = None,
) -> DrawingModel:
    if not plan.units_per_point or plan.units_per_point <= 0:
        raise ValueError(f"{plan.name}: a confirmed scale is required.")
    document = pymupdf.open(pdf_path)
    try:
        page = document[plan.page_index]
        page_rotation = page.rotation
        display_crop = plan.crop.normalized()
        crop = unrotated_page_rect(page, display_crop)
        exclusions = [
            unrotated_page_rect(page, rect.normalized())
            for rect in plan.exclude_regions
        ]
        page.set_rotation(0)
        effective_rotation = (plan.rotation - page_rotation) % 360
        transform = CadTransform(crop, plan.units_per_point, effective_rotation)
        tolerance = max(plan.units_per_point * 0.02, 1e-6)
        model = DrawingModel(
            name=plan.name,
            source_page=plan.page_index + 1,
            crop=display_crop,
            rotation=plan.rotation,
            units=plan.units,
            units_per_point=plan.units_per_point,
            scale_label=plan.scale_label,
            confidence=plan.confidence,
            width=transform.width,
            height=transform.height,
            remove_text=plan.remove_text,
            orthogonal_only=plan.orthogonal_only,
            angle_tolerance=plan.angle_tolerance,
            exclude_regions=list(plan.exclude_regions),
            warnings=list(plan.warnings),
        )
        segments_by_style: dict[
            tuple[str, StrokeStyle],
            list[tuple[Point, Point]],
        ] = defaultdict(list)
        text_regions: list[RectModel] = []
        if plan.remove_text:
            if tesseract_available():
                if progress:
                    progress("Finding native and outlined text for removal")
                text_regions = [
                    _expanded_rect(line.rect, 1.5, crop)
                    for line in ocr_lines(
                        page,
                        pymupdf.Rect(crop.x0, crop.y0, crop.x1, crop.y1),
                        scale=2.5,
                    )
                    if line.confidence >= 0.35
                ]
            else:
                model.warnings.append(
                    "Tesseract OCR was unavailable; outlined-word removal used geometric heuristics and may miss more labels."
                )
        drawings = page.get_drawings(extended=True)
        if progress:
            progress(f"Reading {len(drawings):,} PDF drawing records")
        for index, path in enumerate(drawings, 1):
            if progress and index % 100 == 0:
                progress(f"Building drawing model {index:,}/{len(drawings):,}")
            if path.get("rect") is None or path.get("type") in {"clip", "group"}:
                continue
            path_rect = pymupdf.Rect(path["rect"])
            if not _rect_touches_crop(path_rect, crop):
                continue
            if plan.remove_text and _rect_is_text_geometry(
                path_rect,
                text_regions,
                len(path.get("items", [])),
            ):
                model.unsupported["filtered_text_outlines"] += 1
                continue
            layer = _layer_for_path(path)
            style = _stroke_style(path, plan.units_per_point)
            segments, curves, rings, unsupported = _path_parts(path)
            model.unsupported["path_items"] += unsupported
            for start, end in segments:
                if plan.remove_text and _segment_is_text_geometry(
                    start, end, text_regions
                ):
                    model.unsupported["filtered_text_segments"] += 1
                    continue
                original_clipped = clip_segment(start, end, crop)
                clipped_segments = clip_segment_excluding(
                    start,
                    end,
                    crop,
                    exclusions,
                )
                if exclusions and clipped_segments != original_clipped:
                    model.unsupported["filtered_exclusion_segments"] += 1
                for clipped_start, clipped_end in clipped_segments:
                    transformed_start = transform.point(clipped_start)
                    transformed_end = transform.point(clipped_end)
                    if plan.orthogonal_only and not _is_orthogonal_segment(
                        transformed_start,
                        transformed_end,
                        plan.angle_tolerance,
                    ):
                        model.unsupported["filtered_non_orthogonal_segments"] += 1
                        continue
                    segments_by_style[(layer, style)].append(
                        (transformed_start, transformed_end)
                    )
            for control_points in curves:
                if plan.orthogonal_only:
                    model.unsupported["filtered_curves"] += 1
                    continue
                if exclusions and (
                    any(_point_in_any(point, exclusions) for point in control_points)
                    or _rect_touches_any(
                        pymupdf.Rect(
                            min(point[0] for point in control_points),
                            min(point[1] for point in control_points),
                            max(point[0] for point in control_points),
                            max(point[1] for point in control_points),
                        ),
                        exclusions,
                    )
                ):
                    flattened = flatten_cubic(control_points)
                    for start, end in zip(flattened, flattened[1:]):
                        for clipped_start, clipped_end in clip_segment_excluding(
                            start,
                            end,
                            crop,
                            exclusions,
                        ):
                            segments_by_style[(layer, style)].append(
                                (
                                    transform.point(clipped_start),
                                    transform.point(clipped_end),
                                )
                            )
                    model.unsupported["filtered_exclusion_curves"] += 1
                    continue
                if all(_inside(point, crop) for point in control_points):
                    transformed = tuple(transform.point(point) for point in control_points)
                    if any(
                        math.dist(transformed[i], transformed[i + 1]) > tolerance
                        for i in range(3)
                    ):
                        model.curves.append(CubicBezier(layer, transformed, style))
                    else:
                        model.unsupported["degenerate_curves"] += 1
                else:
                    flattened = flatten_cubic(control_points)
                    for start, end in zip(flattened, flattened[1:]):
                        for clipped_start, clipped_end in clip_segment(start, end, crop):
                            segments_by_style[(layer, style)].append(
                                (transform.point(clipped_start), transform.point(clipped_end))
                            )
                    model.warnings.append(
                        "One or more clipped curves were flattened to linework."
                    )
            if path.get("fill") is not None and not plan.orthogonal_only:
                fill_layer = _layer_for_path(path, fill=True)
                for ring in rings:
                    ring_rect = pymupdf.Rect(
                        min(point[0] for point in ring),
                        min(point[1] for point in ring),
                        max(point[0] for point in ring),
                        max(point[1] for point in ring),
                    )
                    if (
                        len(ring) >= 3
                        and all(_inside(point, crop) for point in ring)
                        and not _rect_touches_any(
                            ring_rect,
                            exclusions,
                        )
                    ):
                        model.polygons.append(
                            Polygon(
                                layer=fill_layer,
                                points=[transform.point(point) for point in ring],
                                fill=_color(path.get("fill")),
                                opacity=_number_or(path.get("fill_opacity"), 1),
                            )
                        )
                    elif _rect_touches_any(ring_rect, exclusions):
                        model.unsupported["filtered_exclusion_fills"] += 1

        for (layer, style), segments in segments_by_style.items():
            for chain in join_collinear_segments(
                dedupe_segments(segments, tolerance),
                endpoint_tolerance=tolerance,
            ):
                model.polylines.append(Polyline(layer, chain, style))

        if not plan.remove_text:
            text_dict = page.get_text(
                "dict",
                clip=pymupdf.Rect(crop.x0, crop.y0, crop.x1, crop.y1),
            )
            for block in text_dict.get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    rotation = _text_rotation(
                        transform, tuple(line.get("dir", (1.0, 0.0)))
                    )
                    for span in line.get("spans", []):
                        value = span.get("text", "").strip()
                        origin = span.get("origin")
                        if not value or not origin or not _inside(tuple(origin), crop, 1):
                            continue
                        span_bbox = span.get("bbox")
                        if span_bbox and _rect_touches_any(
                            pymupdf.Rect(span_bbox), exclusions
                        ):
                            model.unsupported["filtered_exclusion_text"] += 1
                            continue
                        model.texts.append(
                            DrawingText(
                                layer="PDF-TEXT",
                                text=value,
                                insert=transform.point(tuple(origin)),
                                height=max(
                                    _number_or(span.get("size"), 1)
                                    * plan.units_per_point,
                                    tolerance,
                                ),
                                rotation=rotation,
                                color=_text_color(int(span.get("color", 0))),
                                opacity=_number_or(span.get("alpha"), 255) / 255,
                                font_family=span.get("font", "sans-serif"),
                            )
                        )
    finally:
        document.close()
    if model.unsupported["path_items"]:
        model.warnings.append(
            f"Unsupported PDF path items: {model.unsupported['path_items']}."
        )
    if plan.remove_text:
        model.warnings.append(
            "Text cleanup removed native text and OCR-matched outlined lettering."
        )
    if plan.orthogonal_only:
        model.warnings.append(
            f"Orthogonal cleanup kept linework within ±{plan.angle_tolerance:g}° of horizontal or vertical and removed curves and fills."
        )
    if plan.exclude_regions:
        model.warnings.append(
            f"Applied {len(plan.exclude_regions)} exclusion mask"
            + ("s." if len(plan.exclude_regions) != 1 else ".")
        )
    model.warnings = sorted(set(model.warnings))
    return model
