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
    color_hex,
    dedupe_segments,
    flatten_cubic,
    join_collinear_segments,
    safe_layer_name,
)
from .models import PlanRegion, RectModel


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


def build_drawing_model(
    pdf_path: Path,
    plan: PlanRegion,
    progress: ProgressCallback | None = None,
) -> DrawingModel:
    if not plan.units_per_point or plan.units_per_point <= 0:
        raise ValueError(f"{plan.name}: a confirmed scale is required.")
    crop = plan.crop.normalized()
    transform = CadTransform(crop, plan.units_per_point, plan.rotation)
    tolerance = max(plan.units_per_point * 0.02, 1e-6)
    model = DrawingModel(
        name=plan.name,
        source_page=plan.page_index + 1,
        crop=crop,
        rotation=plan.rotation,
        units=plan.units,
        units_per_point=plan.units_per_point,
        scale_label=plan.scale_label,
        confidence=plan.confidence,
        width=transform.width,
        height=transform.height,
        warnings=list(plan.warnings),
    )
    segments_by_style: dict[tuple[str, StrokeStyle], list[tuple[Point, Point]]] = defaultdict(list)

    document = pymupdf.open(pdf_path)
    try:
        page = document[plan.page_index]
        drawings = page.get_drawings(extended=True)
        if progress:
            progress(f"Reading {len(drawings):,} PDF drawing records")
        for index, path in enumerate(drawings, 1):
            if progress and index % 100 == 0:
                progress(f"Building drawing model {index:,}/{len(drawings):,}")
            if path.get("rect") is None or path.get("type") in {"clip", "group"}:
                continue
            if not _rect_touches_crop(pymupdf.Rect(path["rect"]), crop):
                continue
            layer = _layer_for_path(path)
            style = _stroke_style(path, plan.units_per_point)
            segments, curves, rings, unsupported = _path_parts(path)
            model.unsupported["path_items"] += unsupported
            for start, end in segments:
                for clipped_start, clipped_end in clip_segment(start, end, crop):
                    segments_by_style[(layer, style)].append(
                        (transform.point(clipped_start), transform.point(clipped_end))
                    )
            for control_points in curves:
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
            if path.get("fill") is not None:
                fill_layer = _layer_for_path(path, fill=True)
                for ring in rings:
                    if len(ring) >= 3 and all(_inside(point, crop) for point in ring):
                        model.polygons.append(
                            Polygon(
                                layer=fill_layer,
                                points=[transform.point(point) for point in ring],
                                fill=_color(path.get("fill")),
                                opacity=_number_or(path.get("fill_opacity"), 1),
                            )
                        )

        for (layer, style), segments in segments_by_style.items():
            for chain in join_collinear_segments(
                dedupe_segments(segments, tolerance),
                endpoint_tolerance=tolerance,
            ):
                model.polylines.append(Polyline(layer, chain, style))

        text_dict = page.get_text(
            "dict",
            clip=pymupdf.Rect(crop.x0, crop.y0, crop.x1, crop.y1),
        )
        for block in text_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                rotation = _text_rotation(transform, tuple(line.get("dir", (1.0, 0.0))))
                for span in line.get("spans", []):
                    value = span.get("text", "").strip()
                    origin = span.get("origin")
                    if not value or not origin or not _inside(tuple(origin), crop, 1):
                        continue
                    model.texts.append(
                        DrawingText(
                            layer="PDF-TEXT",
                            text=value,
                            insert=transform.point(tuple(origin)),
                            height=max(
                                _number_or(span.get("size"), 1) * plan.units_per_point,
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
    if sum(model.unsupported.values()):
        model.warnings.append(
            f"Unsupported PDF path items: {sum(model.unsupported.values())}."
        )
    model.warnings = sorted(set(model.warnings))
    return model
