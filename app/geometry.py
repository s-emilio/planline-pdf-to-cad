from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable, Iterator, Sequence

from shapely.geometry import GeometryCollection, LineString, MultiLineString, box
from shapely.ops import unary_union

from .models import RectModel


Point = tuple[float, float]
Segment = tuple[Point, Point]


@dataclass(frozen=True)
class CadTransform:
    crop: RectModel
    units_per_point: float
    rotation: int = 0

    def _raw(self, point: Point) -> Point:
        x = point[0] - self.crop.x0
        y = self.crop.y1 - point[1]
        if self.rotation == 0:
            return x, y
        if self.rotation == 90:
            return -y, x
        if self.rotation == 180:
            return -x, -y
        if self.rotation == 270:
            return y, -x
        raise ValueError(f"Unsupported rotation: {self.rotation}")

    @property
    def offset(self) -> Point:
        corners = (
            (self.crop.x0, self.crop.y0),
            (self.crop.x1, self.crop.y0),
            (self.crop.x1, self.crop.y1),
            (self.crop.x0, self.crop.y1),
        )
        transformed = [self._raw(point) for point in corners]
        return -min(p[0] for p in transformed), -min(p[1] for p in transformed)

    def point(self, point: Point) -> Point:
        x, y = self._raw(point)
        ox, oy = self.offset
        return (x + ox) * self.units_per_point, (y + oy) * self.units_per_point

    @property
    def width(self) -> float:
        if self.rotation in {0, 180}:
            return (self.crop.x1 - self.crop.x0) * self.units_per_point
        return (self.crop.y1 - self.crop.y0) * self.units_per_point

    @property
    def height(self) -> float:
        if self.rotation in {0, 180}:
            return (self.crop.y1 - self.crop.y0) * self.units_per_point
        return (self.crop.x1 - self.crop.x0) * self.units_per_point


def rect_intersection(a: RectModel, b: RectModel) -> RectModel | None:
    x0, y0 = max(a.x0, b.x0), max(a.y0, b.y0)
    x1, y1 = min(a.x1, b.x1), min(a.y1, b.y1)
    if x1 <= x0 or y1 <= y0:
        return None
    return RectModel(x0=x0, y0=y0, x1=x1, y1=y1)


def rect_iou(a: RectModel, b: RectModel) -> float:
    intersection = rect_intersection(a, b)
    if not intersection:
        return 0.0
    inter_area = (intersection.x1 - intersection.x0) * (intersection.y1 - intersection.y0)
    area_a = (a.x1 - a.x0) * (a.y1 - a.y0)
    area_b = (b.x1 - b.x0) * (b.y1 - b.y0)
    return inter_area / max(area_a + area_b - inter_area, 1e-9)


def expand_rect(rect: RectModel, amount: float, bounds: RectModel) -> RectModel:
    return RectModel(
        x0=max(bounds.x0, rect.x0 - amount),
        y0=max(bounds.y0, rect.y0 - amount),
        x1=min(bounds.x1, rect.x1 + amount),
        y1=min(bounds.y1, rect.y1 + amount),
    )


def color_hex(color: Sequence[float] | None) -> str:
    if not color:
        return "000000"
    values = [max(0, min(255, round(component * 255))) for component in color[:3]]
    return "".join(f"{value:02X}" for value in values)


def safe_layer_name(name: str, fallback: str = "PDF-LINEWORK") -> str:
    value = re.sub(r'[<>/\\":;?*|=,]', "-", (name or "").strip())
    value = re.sub(r"\s+", " ", value).strip(" .")
    return (value or fallback)[:255]


def line_key(start: Point, end: Point, tolerance: float) -> tuple[int, int, int, int]:
    if end < start:
        start, end = end, start
    scale = 1.0 / max(tolerance, 1e-9)
    return tuple(round(value * scale) for value in (*start, *end))


def dedupe_segments(segments: Iterable[Segment], tolerance: float) -> list[Segment]:
    output: list[Segment] = []
    seen: set[tuple[int, int, int, int]] = set()
    for start, end in segments:
        if math.dist(start, end) <= tolerance:
            continue
        key = line_key(start, end, tolerance)
        if key not in seen:
            seen.add(key)
            output.append((start, end))
    return output


def _collinear(a: Point, b: Point, c: Point, angle_tolerance: float) -> bool:
    ab = (b[0] - a[0], b[1] - a[1])
    bc = (c[0] - b[0], c[1] - b[1])
    len_ab, len_bc = math.hypot(*ab), math.hypot(*bc)
    if len_ab <= 1e-9 or len_bc <= 1e-9:
        return True
    cross = abs(ab[0] * bc[1] - ab[1] * bc[0]) / (len_ab * len_bc)
    dot = (ab[0] * bc[0] + ab[1] * bc[1]) / (len_ab * len_bc)
    return cross <= math.sin(angle_tolerance) and dot > 0


def join_collinear_segments(
    segments: Iterable[Segment],
    endpoint_tolerance: float,
    angle_tolerance_degrees: float = 0.25,
) -> list[list[Point]]:
    remaining = [list(segment) for segment in segments]
    chains: list[list[Point]] = []
    angle_tolerance = math.radians(angle_tolerance_degrees)

    while remaining:
        chain = remaining.pop()
        changed = True
        while changed:
            changed = False
            for index, segment in enumerate(remaining):
                candidates = (
                    (chain[-1], segment[0], segment[1], "append"),
                    (chain[-1], segment[1], segment[0], "append"),
                    (chain[0], segment[1], segment[0], "prepend"),
                    (chain[0], segment[0], segment[1], "prepend"),
                )
                for touching, near, far, operation in candidates:
                    if math.dist(touching, near) > endpoint_tolerance:
                        continue
                    if operation == "append":
                        previous = chain[-2] if len(chain) > 1 else chain[-1]
                        if not _collinear(previous, touching, far, angle_tolerance):
                            continue
                        chain.append(far)
                    else:
                        following = chain[1] if len(chain) > 1 else chain[0]
                        if not _collinear(far, touching, following, angle_tolerance):
                            continue
                        chain.insert(0, far)
                    remaining.pop(index)
                    changed = True
                    break
                if changed:
                    break
        chains.append(chain)
    return chains


def clip_segment(start: Point, end: Point, rect: RectModel) -> list[Segment]:
    clipped = LineString([start, end]).intersection(box(rect.x0, rect.y0, rect.x1, rect.y1))
    output: list[Segment] = []
    geometries: Iterator = iter(())
    if isinstance(clipped, LineString):
        geometries = iter([clipped])
    elif isinstance(clipped, (MultiLineString, GeometryCollection)):
        geometries = (item for item in clipped.geoms if isinstance(item, LineString))
    for geometry in geometries:
        coords = list(geometry.coords)
        for index in range(len(coords) - 1):
            output.append((coords[index], coords[index + 1]))
    return output


def clip_segment_excluding(
    start: Point,
    end: Point,
    crop: RectModel,
    exclusions: Sequence[RectModel],
) -> list[Segment]:
    geometry = LineString([start, end]).intersection(
        box(crop.x0, crop.y0, crop.x1, crop.y1)
    )
    if exclusions and not geometry.is_empty:
        masks = unary_union(
            [box(rect.x0, rect.y0, rect.x1, rect.y1) for rect in exclusions]
        )
        geometry = geometry.difference(masks)
    output: list[Segment] = []
    geometries: Iterator = iter(())
    if isinstance(geometry, LineString):
        geometries = iter([geometry])
    elif isinstance(geometry, (MultiLineString, GeometryCollection)):
        geometries = (
            item for item in geometry.geoms if isinstance(item, LineString)
        )
    for line in geometries:
        coords = list(line.coords)
        for index in range(len(coords) - 1):
            output.append((coords[index], coords[index + 1]))
    return output


def cubic_point(points: Sequence[Point], t: float) -> Point:
    p0, p1, p2, p3 = points
    mt = 1 - t
    return (
        mt**3 * p0[0] + 3 * mt**2 * t * p1[0] + 3 * mt * t**2 * p2[0] + t**3 * p3[0],
        mt**3 * p0[1] + 3 * mt**2 * t * p1[1] + 3 * mt * t**2 * p2[1] + t**3 * p3[1],
    )


def flatten_cubic(points: Sequence[Point], steps: int = 24) -> list[Point]:
    return [cubic_point(points, index / steps) for index in range(steps + 1)]
