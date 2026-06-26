from __future__ import annotations

import zipfile

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
    assert calibration.json()["calibration"] == {
        "x1": 100,
        "y1": 100,
        "x2": 172,
        "y2": 100,
        "distance": 4,
        "units": "ft",
    }


def test_rejects_non_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "JOBS_ROOT", tmp_path / "jobs")
    client = TestClient(app)
    response = client.post(
        "/api/jobs",
        files={"file": ("fake.pdf", b"not a pdf", "application/pdf")},
    )
    assert response.status_code == 400


def test_confirmed_scale_clears_stale_manual_review_warning(
    vector_pdf, tmp_path, monkeypatch
):
    monkeypatch.setattr(jobs, "JOBS_ROOT", tmp_path / "jobs")
    state = jobs.create_job("sample.pdf", vector_pdf.read_bytes())
    plan = jobs.add_plan(state, 0, state.plans[0].crop, "Manual Plan")
    updated = jobs.update_plan(
        state,
        plan.id,
        units_per_point=1,
        scale_confirmed=True,
    )
    assert updated.confirmed is True
    assert updated.warnings == []


def test_persistent_project_api_lifecycle(vector_pdf, tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "JOBS_ROOT", tmp_path / "Planline Projects")
    client = TestClient(app)
    created = client.post(
        "/api/projects",
        data={"name": "Ranch Renovation"},
        files={"file": ("ranch.pdf", vector_pdf.read_bytes(), "application/pdf")},
    )
    assert created.status_code == 200
    project = created.json()
    project_id = project["id"]
    directory = jobs.job_dir(project_id)
    assert (directory / "project.json").is_file()
    assert (directory / "source.pdf").is_file()
    assert project["project_name"] == "Ranch Renovation"

    listed = client.get("/api/projects")
    assert listed.status_code == 200
    assert [item["name"] for item in listed.json()] == ["Ranch Renovation"]

    renamed = client.patch(
        f"/api/projects/{project_id}",
        json={"name": "Ranch Remodel"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["project_name"] == "Ranch Remodel"

    preview = client.get(f"/api/jobs/{project_id}/pages/0/preview")
    assert preview.status_code == 200
    assert (directory / "previews" / "page-001.png").is_file()

    package = client.get(f"/api/projects/{project_id}/package")
    assert package.status_code == 200
    package_path = tmp_path / "download.planline"
    package_path.write_bytes(package.content)
    with zipfile.ZipFile(package_path) as bundle:
        assert "manifest.json" in bundle.namelist()
        assert "source.pdf" in bundle.namelist()
        assert "previews/page-001.png" in bundle.namelist()

    duplicated = client.post(
        f"/api/projects/{project_id}/duplicate",
        json={"name": "Ranch Alternate"},
    )
    assert duplicated.status_code == 200
    duplicate = duplicated.json()
    assert duplicate["id"] != project_id
    assert duplicate["project_name"] == "Ranch Alternate"
    assert jobs.pdf_path(duplicate["id"]).read_bytes() == vector_pdf.read_bytes()

    default_copy = client.post(f"/api/projects/{project_id}/duplicate")
    assert default_copy.status_code == 200
    assert default_copy.json()["project_name"] == "Ranch Remodel Copy"
    assert client.delete(
        f"/api/projects/{default_copy.json()['id']}"
    ).status_code == 204

    archived = client.patch(
        f"/api/projects/{project_id}",
        json={"archived": True},
    )
    assert archived.status_code == 200
    assert archived.json()["archived"] is True
    visible_names = [item["name"] for item in client.get("/api/projects").json()]
    assert visible_names == ["Ranch Alternate"]
    all_names = [
        item["name"]
        for item in client.get("/api/projects?include_archived=true").json()
    ]
    assert set(all_names) == {"Ranch Remodel", "Ranch Alternate"}

    removed = client.delete(f"/api/projects/{duplicate['id']}")
    assert removed.status_code == 204
    assert not jobs.job_dir(duplicate["id"]).exists()


def test_imports_exported_planline_as_new_persistent_project(
    vector_pdf,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(jobs, "JOBS_ROOT", tmp_path / "Planline Projects")
    client = TestClient(app)
    created = client.post(
        "/api/projects",
        data={"name": "Portable House"},
        files={"file": ("house.pdf", vector_pdf.read_bytes(), "application/pdf")},
    ).json()
    original_plan = created["plans"][0]
    updated = client.patch(
        f"/api/jobs/{created['id']}/plans/{original_plan['id']}",
        json={
            "name": "Imported First Floor",
            "remove_text": True,
            "exclude_regions": [
                {
                    "x0": original_plan["crop"]["x0"] + 5,
                    "y0": original_plan["crop"]["y0"] + 5,
                    "x1": original_plan["crop"]["x0"] + 15,
                    "y1": original_plan["crop"]["y0"] + 15,
                }
            ],
        },
    )
    assert updated.status_code == 200
    package = client.get(f"/api/projects/{created['id']}/package")
    assert package.status_code == 200

    imported = client.post(
        "/api/projects/import",
        files={
            "file": (
                "portable-house.planline",
                package.content,
                "application/vnd.planline.project+zip",
            )
        },
    )
    assert imported.status_code == 200
    restored = imported.json()
    assert restored["id"] != created["id"]
    assert restored["project_name"] == "Portable House"
    assert restored["filename"] == "house.pdf"
    assert restored["source_sha256"] == created["source_sha256"]
    assert restored["plans"][0]["name"] == "Imported First Floor"
    assert restored["plans"][0]["remove_text"] is True
    assert len(restored["plans"][0]["exclude_regions"]) == 1
    assert jobs.pdf_path(restored["id"]).read_bytes() == vector_pdf.read_bytes()


def test_project_home_lists_recent_projects(vector_pdf, tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "JOBS_ROOT", tmp_path / "Planline Projects")
    jobs.create_job("sample.pdf", vector_pdf.read_bytes(), "Recent Test")
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "Your projects" in response.text
    assert "Import .planline" in response.text
    assert 'id="recentProjectList"' in response.text
