from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pdf_plan_to_dwg.app import jobs
from pdf_plan_to_dwg.app.main import app


def test_upload_review_and_calibrate(vector_pdf, tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "JOBS_ROOT", tmp_path / "jobs")
    client = TestClient(app)
    response = client.post(
        "/api/jobs",
        files={"file": ("sample.pdf", vector_pdf.read_bytes(), "application/pdf")},
    )
    assert response.status_code == 200
    job = response.json()
    assert job["status"] == "ready"
    assert job["plans"]
    plan = job["plans"][0]

    calibration = client.post(
        f"/api/jobs/{job['id']}/plans/{plan['id']}/calibrate",
        json={
            "x1": 100,
            "y1": 100,
            "x2": 172,
            "y2": 100,
            "distance": 4,
            "units": "ft",
            "name": "Saved Floor Plan",
            "rotation": 90,
            "crop": plan["crop"],
        },
    )
    assert calibration.status_code == 200
    assert calibration.json()["scale_confirmed"] is True
    assert calibration.json()["confirmed"] is True
    assert calibration.json()["name"] == "Saved Floor Plan"
    assert calibration.json()["rotation"] == 90
    assert calibration.json()["units_per_point"] == pytest.approx(48 / 72)


def test_rejects_non_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "JOBS_ROOT", tmp_path / "jobs")
    client = TestClient(app)
    response = client.post(
        "/api/jobs",
        files={"file": ("fake.pdf", b"not a pdf", "application/pdf")},
    )
    assert response.status_code == 400
