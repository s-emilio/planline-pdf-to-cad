from __future__ import annotations

import io
from pathlib import Path

import pymupdf
import pytest
from PIL import Image


@pytest.fixture
def vector_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "vector-plan.pdf"
    document = pymupdf.open()
    page = document.new_page(width=792, height=612)
    for x in range(80, 561, 40):
        page.draw_line((x, 80), (x, 430), width=1)
    for y in range(80, 431, 40):
        page.draw_line((80, y), (560, y), width=1)
    page.draw_rect((80, 80, 560, 430), width=2)
    page.draw_bezier((150, 150), (180, 110), (230, 110), (260, 150), width=1)
    page.draw_rect((300, 180, 360, 240), color=(0, 0, 0), fill=(0.8, 0.8, 0.8))
    page.insert_text(
        (100, 470),
        "FIRST FLOOR PLAN   SCALE: 1/4\" = 1'-0\"",
        fontsize=12,
    )
    page.insert_text((120, 120), "ROOM 101", fontsize=8)
    page.draw_rect((600, 430, 770, 590))
    page.insert_text((615, 455), "PROJECT TITLE BLOCK", fontsize=8)
    document.save(path)
    document.close()
    return path


@pytest.fixture
def raster_pdf(tmp_path: Path) -> Path:
    image = Image.new("RGB", (1200, 900), "white")
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    path = tmp_path / "scan.pdf"
    document = pymupdf.open()
    page = document.new_page(width=600, height=450)
    page.insert_image(page.rect, stream=buffer.getvalue())
    document.save(path)
    document.close()
    return path

