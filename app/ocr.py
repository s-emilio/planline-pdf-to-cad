from __future__ import annotations

import csv
import io
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from .models import RectModel, ScaleCandidate
from .scales import parse_scale_candidates


@dataclass
class OcrLine:
    text: str
    rect: RectModel
    confidence: float


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def parse_tesseract_tsv(
    tsv: str,
    *,
    clip: pymupdf.Rect,
    scale: float,
) -> list[OcrLine]:
    grouped: dict[tuple[str, str, str, str], list[dict[str, str]]] = {}
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t"):
        text = (row.get("text") or "").strip()
        try:
            confidence = float(row.get("conf") or -1)
        except ValueError:
            continue
        if not text or confidence < 0:
            continue
        key = (
            row.get("page_num", ""),
            row.get("block_num", ""),
            row.get("par_num", ""),
            row.get("line_num", ""),
        )
        grouped.setdefault(key, []).append(row)

    lines: list[OcrLine] = []
    for rows in grouped.values():
        rows.sort(key=lambda row: int(row.get("left") or 0))
        left = min(int(row.get("left") or 0) for row in rows)
        top = min(int(row.get("top") or 0) for row in rows)
        right = max(
            int(row.get("left") or 0) + int(row.get("width") or 0)
            for row in rows
        )
        bottom = max(
            int(row.get("top") or 0) + int(row.get("height") or 0)
            for row in rows
        )
        confidences = [float(row["conf"]) for row in rows]
        lines.append(
            OcrLine(
                text=" ".join(row["text"].strip() for row in rows),
                rect=RectModel(
                    x0=clip.x0 + left / scale,
                    y0=clip.y0 + top / scale,
                    x1=clip.x0 + right / scale,
                    y1=clip.y0 + bottom / scale,
                ),
                confidence=sum(confidences) / len(confidences) / 100,
            )
        )
    return lines


def ocr_lines(
    page: pymupdf.Page,
    clip: pymupdf.Rect,
    *,
    scale: float = 3.0,
) -> list[OcrLine]:
    binary = shutil.which("tesseract")
    if not binary:
        return []
    pixmap = page.get_pixmap(
        matrix=pymupdf.Matrix(scale, scale),
        clip=clip,
        colorspace=pymupdf.csGRAY,
        alpha=False,
    )
    with tempfile.TemporaryDirectory(prefix="planline-ocr-") as directory:
        image_path = Path(directory) / "sheet.png"
        pixmap.save(image_path)
        try:
            result = subprocess.run(
                [binary, str(image_path), "stdout", "--psm", "11", "tsv"],
                check=True,
                capture_output=True,
                text=True,
                timeout=75,
            )
        except (subprocess.SubprocessError, OSError):
            return []
    return parse_tesseract_tsv(result.stdout, clip=clip, scale=scale)


def ocr_scale_candidates(page: pymupdf.Page) -> list[ScaleCandidate]:
    if not tesseract_available():
        return []

    bounds = page.rect
    regions = [
        pymupdf.Rect(bounds.x0, bounds.y0 + bounds.height * 0.55, bounds.x1, bounds.y1),
        pymupdf.Rect(bounds.x0 + bounds.width * 0.72, bounds.y0, bounds.x1, bounds.y1),
    ]
    lines: list[OcrLine] = []
    for region in regions:
        lines.extend(ocr_lines(page, region, scale=3.5))

    candidates: list[ScaleCandidate] = []
    seen: set[tuple[str, int, int]] = set()
    for line in lines:
        line_confidence = max(0.45, min(0.82, line.confidence * 0.85))
        for candidate in parse_scale_candidates(
            line.text,
            source="OCR",
            rect=line.rect,
            confidence=line_confidence,
        ):
            key = (
                candidate.units,
                round(candidate.units_per_point * 1_000_000),
                round((line.rect.x0 + line.rect.x1) / 20),
            )
            if key not in seen:
                seen.add(key)
                candidates.append(candidate)
    return candidates
