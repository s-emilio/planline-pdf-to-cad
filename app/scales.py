from __future__ import annotations

import math
import re

from .models import ScaleCandidate


IMPERIAL_RE = re.compile(
    r"(?P<num>\d+)?\s*(?P<frac>\d+\s*/\s*\d+)?\s*[\"″]\s*"
    r"=\s*(?P<feet>\d+)\s*['′]\s*(?:-\s*(?P<inches>\d+)\s*[\"″])?",
    re.IGNORECASE,
)
METRIC_RATIO_RE = re.compile(r"\b1\s*:\s*(?P<ratio>\d{1,5}(?:\.\d+)?)\b")
METRIC_WORD_RE = re.compile(
    r"(?P<paper>\d+(?:\.\d+)?)\s*(?P<punit>mm|cm)\s*"
    r"=\s*(?P<real>\d+(?:\.\d+)?)\s*(?P<runit>mm|cm|m)\b",
    re.IGNORECASE,
)


def _mixed_number(whole: str | None, fraction: str | None) -> float:
    value = float(whole or 0)
    if fraction:
        numerator, denominator = fraction.replace(" ", "").split("/", 1)
        value += float(numerator) / float(denominator)
    return value


def parse_scale_candidates(text: str) -> list[ScaleCandidate]:
    candidates: list[ScaleCandidate] = []
    seen: set[tuple[str, int]] = set()

    for match in IMPERIAL_RE.finditer(text):
        paper_inches = _mixed_number(match.group("num"), match.group("frac"))
        real_inches = float(match.group("feet")) * 12 + float(match.group("inches") or 0)
        if paper_inches <= 0 or real_inches <= 0:
            continue
        units_per_point = (real_inches / paper_inches) / 72.0
        key = ("in", round(units_per_point * 1_000_000))
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            ScaleCandidate(
                label=match.group(0).strip(),
                units="in",
                units_per_point=units_per_point,
                confidence=0.92,
                source="printed imperial scale",
            )
        )

    for match in METRIC_RATIO_RE.finditer(text):
        ratio = float(match.group("ratio"))
        if ratio <= 0:
            continue
        units_per_point = ratio * 25.4 / 72.0
        key = ("mm", round(units_per_point * 1_000_000))
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            ScaleCandidate(
                label=match.group(0).strip(),
                units="mm",
                units_per_point=units_per_point,
                confidence=0.88,
                source="printed metric ratio",
            )
        )

    unit_to_mm = {"mm": 1.0, "cm": 10.0, "m": 1000.0}
    for match in METRIC_WORD_RE.finditer(text):
        paper_mm = float(match.group("paper")) * unit_to_mm[match.group("punit").lower()]
        real_mm = float(match.group("real")) * unit_to_mm[match.group("runit").lower()]
        if paper_mm <= 0 or real_mm <= 0:
            continue
        ratio = real_mm / paper_mm
        units_per_point = ratio * 25.4 / 72.0
        key = ("mm", round(units_per_point * 1_000_000))
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            ScaleCandidate(
                label=match.group(0).strip(),
                units="mm",
                units_per_point=units_per_point,
                confidence=0.9,
                source="printed metric scale",
            )
        )

    return candidates


def calibration_units_per_point(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    distance: float,
    units: str,
) -> tuple[str, float]:
    pdf_distance = math.hypot(x2 - x1, y2 - y1)
    if pdf_distance <= 1e-9:
        raise ValueError("Calibration points must be different.")

    if units in {"in", "ft"}:
        real_distance = distance * (12.0 if units == "ft" else 1.0)
        output_units = "in"
    else:
        multiplier = {"mm": 1.0, "cm": 10.0, "m": 1000.0}[units]
        real_distance = distance * multiplier
        output_units = "mm"
    return output_units, real_distance / pdf_distance

