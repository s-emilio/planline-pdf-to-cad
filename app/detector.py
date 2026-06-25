from __future__ import annotations

import math
from dataclasses import dataclass

import pymupdf

from .geometry import expand_rect, rect_iou
from .models import PageInfo, PlanRegion, RectModel
from .scales import parse_scale_candidates


PLAN_WORDS = (
    "floor plan",
    "foundation plan",
    "roof plan",
    "reflected ceiling plan",
    "level plan",
    "enlarged plan",
)


@dataclass
class Detection:
    rect: RectModel
    score: float
    notes: list[str]


def drawable_paths(drawings: list[dict]) -> list[dict]:
    """Return paint records that have usable geometry bounds.

    Extended PyMuPDF drawing output can also include ``clip`` and ``group``
    records. Some producer libraries, including Foxit Quick PDF Library, omit
    ``rect`` from clip records. ``cluster_drawings()`` expects every input
    record to contain that key, so those records must not be passed to it.
    """
    return [
        path
        for path in drawings
        if path.get("rect") is not None and path.get("type") not in {"clip", "group"}
    ]


def _rect(rect: pymupdf.Rect) -> RectModel:
    return RectModel(x0=rect.x0, y0=rect.y0, x1=rect.x1, y1=rect.y1)


def _contains_point(rect: RectModel, x: float, y: float) -> bool:
    return rect.x0 <= x <= rect.x1 and rect.y0 <= y <= rect.y1


def _image_coverage(page: pymupdf.Page) -> float:
    page_area = max(page.rect.width * page.rect.height, 1)
    area = 0.0
    for image in page.get_image_info():
        bbox = pymupdf.Rect(image["bbox"]) & page.rect
        area += max(0, bbox.width) * max(0, bbox.height)
    return min(1.0, area / page_area)


def _item_orientation_score(item: tuple) -> tuple[int, int]:
    kind = item[0]
    if kind == "l":
        start, end = item[1], item[2]
        dx, dy = abs(end.x - start.x), abs(end.y - start.y)
        orthogonal = dx <= 0.5 or dy <= 0.5
        return 1, int(orthogonal)
    if kind == "re":
        return 4, 4
    if kind == "qu":
        return 4, 0
    return 1, 0


def analyze_page(page: pymupdf.Page, page_index: int) -> tuple[PageInfo, list[PlanRegion]]:
    drawings = page.get_drawings(extended=True)
    paint_paths = drawable_paths(drawings)
    vector_items = sum(len(path.get("items", [])) for path in paint_paths)
    image_coverage = _image_coverage(page)
    raster_only = vector_items < 10 and image_coverage >= 0.35
    text = page.get_text("text")
    scale_candidates = parse_scale_candidates(text)

    page_info = PageInfo(
        index=page_index,
        width=page.rect.width,
        height=page.rect.height,
        rotation=page.rotation,
        vector_paths=len(paint_paths),
        vector_items=vector_items,
        image_coverage=image_coverage,
        raster_only=raster_only,
        scale_candidates=scale_candidates,
    )
    if raster_only or vector_items < 4:
        return page_info, []

    page_bounds = RectModel(x0=0, y0=0, x1=page.rect.width, y1=page.rect.height)
    page_area = page.rect.width * page.rect.height
    clusters = page.cluster_drawings(
        drawings=paint_paths,
        x_tolerance=8,
        y_tolerance=8,
    )
    if not clusters:
        clusters = [pymupdf.Rect(path["rect"]) for path in paint_paths]

    words = page.get_text("words")
    detections: list[Detection] = []
    for cluster in clusters:
        rect = expand_rect(_rect(cluster), 12, page_bounds)
        width, height = rect.x1 - rect.x0, rect.y1 - rect.y0
        area_ratio = width * height / max(page_area, 1)
        if area_ratio < 0.025 or width < page.rect.width * 0.14 or height < page.rect.height * 0.14:
            continue

        path_count = 0
        total_primitives = 0
        orthogonal_primitives = 0
        for path in paint_paths:
            path_rect = _rect(pymupdf.Rect(path["rect"]))
            if rect_iou(rect, path_rect) > 0 or (
                path_rect.x0 >= rect.x0
                and path_rect.y0 >= rect.y0
                and path_rect.x1 <= rect.x1
                and path_rect.y1 <= rect.y1
            ):
                path_count += 1
                for item in path.get("items", []):
                    total, orthogonal = _item_orientation_score(item)
                    total_primitives += total
                    orthogonal_primitives += orthogonal

        if total_primitives < 8:
            continue

        contained_text = " ".join(
            word[4]
            for word in words
            if _contains_point(rect, (word[0] + word[2]) / 2, (word[1] + word[3]) / 2)
        ).lower()
        keyword_hits = sum(word in contained_text for word in PLAN_WORDS)
        orthogonality = orthogonal_primitives / max(total_primitives, 1)
        density = min(1.0, total_primitives / max(width * height / 1500, 1))
        area_score = 1.0 - min(1.0, abs(area_ratio - 0.35) / 0.5)
        title_block_penalty = 0.0
        if rect.x0 > page.rect.width * 0.68 and area_ratio < 0.22:
            title_block_penalty += 0.45
        if rect.y0 > page.rect.height * 0.72 and area_ratio < 0.22:
            title_block_penalty += 0.35

        score = (
            0.32 * density
            + 0.28 * orthogonality
            + 0.2 * area_score
            + min(0.3, keyword_hits * 0.2)
            - title_block_penalty
        )
        notes = [
            f"{total_primitives} vector primitives",
            f"{orthogonality:.0%} orthogonal linework",
        ]
        if keyword_hits:
            notes.append("plan title found")
        if title_block_penalty:
            notes.append("possible title-block overlap")
        detections.append(Detection(rect=rect, score=max(0, min(1, score)), notes=notes))

    detections.sort(key=lambda item: item.score, reverse=True)
    kept: list[Detection] = []
    for detection in detections:
        if detection.score < 0.2:
            continue
        if any(rect_iou(detection.rect, existing.rect) > 0.45 for existing in kept):
            continue
        kept.append(detection)
        if len(kept) == 4:
            break

    if not kept:
        content_rects = [_rect(pymupdf.Rect(path["rect"])) for path in paint_paths]
        if content_rects:
            rect = RectModel(
                x0=max(0, min(item.x0 for item in content_rects) - 8),
                y0=max(0, min(item.y0 for item in content_rects) - 8),
                x1=min(page.rect.width, max(item.x1 for item in content_rects) + 8),
                y1=min(page.rect.height, max(item.y1 for item in content_rects) + 8),
            )
            kept.append(
                Detection(
                    rect=rect,
                    score=0.25,
                    notes=["fallback content bounds; crop review required"],
                )
            )

    plans: list[PlanRegion] = []
    for index, detection in enumerate(kept, 1):
        candidate = scale_candidates[0] if scale_candidates else None
        plans.append(
            PlanRegion(
                id=f"p{page_index + 1}-{index}",
                page_index=page_index,
                name=f"Page {page_index + 1} Plan {index}",
                crop=detection.rect,
                confidence=round(detection.score, 3),
                units=candidate.units if candidate else "in",
                units_per_point=candidate.units_per_point if candidate else None,
                scale_label=candidate.label if candidate else None,
                detection_notes=detection.notes,
                warnings=(
                    ["Low-confidence crop: review and confirm before export."]
                    if detection.score < 0.6
                    else []
                )
                + ([] if candidate else ["No drawing scale detected. Calibrate manually."]),
            )
        )
    return page_info, plans


def analyze_document(pdf_path: str) -> tuple[list[PageInfo], list[PlanRegion], list[str]]:
    document = pymupdf.open(pdf_path)
    pages: list[PageInfo] = []
    plans: list[PlanRegion] = []
    warnings: list[str] = []
    try:
        for index, page in enumerate(document):
            info, page_plans = analyze_page(page, index)
            pages.append(info)
            plans.extend(page_plans)
            if info.raster_only:
                warnings.append(
                    f"Page {index + 1} appears raster-only and was not traced."
                )
    finally:
        document.close()
    if not plans:
        warnings.append("No convertible vector plan regions were detected.")
    return pages, plans, warnings
