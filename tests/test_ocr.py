from __future__ import annotations

import pymupdf
import pytest

from pdf_plan_to_dwg.app.ocr import parse_tesseract_tsv


def test_parses_tesseract_words_into_positioned_lines():
    tsv = "\n".join(
        [
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
            "5\t1\t1\t1\t1\t1\t20\t30\t40\t10\t90\tSCALE:",
            "5\t1\t1\t1\t1\t2\t65\t30\t30\t10\t80\t1:100",
        ]
    )
    lines = parse_tesseract_tsv(
        tsv,
        clip=pymupdf.Rect(100, 200, 500, 600),
        scale=2,
    )
    assert len(lines) == 1
    assert lines[0].text == "SCALE: 1:100"
    assert lines[0].confidence == pytest.approx(0.85)
    assert lines[0].rect.x0 == pytest.approx(110)
    assert lines[0].rect.y0 == pytest.approx(215)
    assert lines[0].rect.x1 == pytest.approx(147.5)
