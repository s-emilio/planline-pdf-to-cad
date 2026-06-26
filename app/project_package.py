from __future__ import annotations

import hashlib
import json
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping

from pydantic import ValidationError

from .project_models import (
    PROJECT_FORMAT,
    PROJECT_FORMAT_VERSION,
    PROJECT_SOURCE_PATH,
    ProjectManifest,
)


MAX_PACKAGE_FILES = 500
MAX_PACKAGE_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_MANIFEST_BYTES = 5 * 1024 * 1024


class ProjectPackageError(ValueError):
    pass


@dataclass(frozen=True)
class LoadedProjectPackage:
    manifest: ProjectManifest
    source_pdf: bytes
    previews: dict[str, bytes]
    exports: dict[str, bytes]


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def write_project_package(
    destination: Path,
    manifest: ProjectManifest,
    source_pdf: bytes,
    *,
    previews: Mapping[str, bytes | Path] | None = None,
    exports: Mapping[str, bytes | Path] | None = None,
) -> Path:
    if not source_pdf.startswith(b"%PDF"):
        raise ProjectPackageError("Project source is not a PDF.")
    checksum = sha256_bytes(source_pdf)
    if checksum != manifest.source_pdf.sha256:
        raise ProjectPackageError("Source PDF checksum does not match the manifest.")
    if len(source_pdf) != manifest.source_pdf.size_bytes:
        raise ProjectPackageError("Source PDF size does not match the manifest.")

    destination = Path(destination)
    if destination.suffix.lower() != ".planline":
        destination = destination.with_suffix(".planline")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".planline.tmp")
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as bundle:
            bundle.writestr(
                "manifest.json",
                manifest.model_dump_json(indent=2, exclude_none=True),
            )
            bundle.writestr(PROJECT_SOURCE_PATH, source_pdf)
            _write_group(bundle, "previews", previews or {})
            _write_group(bundle, "exports", exports or {})
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def read_project_package(path: Path) -> LoadedProjectPackage:
    try:
        with zipfile.ZipFile(path) as bundle:
            members = bundle.infolist()
            _validate_members(members)
            names = {member.filename for member in members}
            if "manifest.json" not in names:
                raise ProjectPackageError("Project package is missing manifest.json.")
            if PROJECT_SOURCE_PATH not in names:
                raise ProjectPackageError("Project package is missing source.pdf.")
            manifest_info = bundle.getinfo("manifest.json")
            if manifest_info.file_size > MAX_MANIFEST_BYTES:
                raise ProjectPackageError("Project manifest is too large.")
            manifest = _parse_manifest(bundle.read("manifest.json"))
            source_pdf = bundle.read(PROJECT_SOURCE_PATH)
            if not source_pdf.startswith(b"%PDF"):
                raise ProjectPackageError("Packaged source.pdf is not a PDF.")
            if len(source_pdf) != manifest.source_pdf.size_bytes:
                raise ProjectPackageError("Packaged PDF size does not match the manifest.")
            if sha256_bytes(source_pdf) != manifest.source_pdf.sha256:
                raise ProjectPackageError(
                    "Packaged PDF checksum does not match the manifest."
                )
            previews = _read_group(bundle, members, "previews/")
            exports = _read_group(bundle, members, "exports/")
            return LoadedProjectPackage(
                manifest=manifest,
                source_pdf=source_pdf,
                previews=previews,
                exports=exports,
            )
    except zipfile.BadZipFile as exc:
        raise ProjectPackageError("Project file is not a valid ZIP container.") from exc


def _parse_manifest(content: bytes) -> ProjectManifest:
    try:
        raw = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectPackageError("Project manifest is not valid JSON.") from exc
    if not isinstance(raw, dict):
        raise ProjectPackageError("Project manifest must be a JSON object.")
    if raw.get("format") != PROJECT_FORMAT:
        raise ProjectPackageError("Unsupported project file format.")
    version = raw.get("version")
    if version == 0:
        raw = _migrate_v0_manifest(raw)
    elif version != PROJECT_FORMAT_VERSION:
        raise ProjectPackageError(f"Unsupported project manifest version: {version!r}.")
    try:
        return ProjectManifest.model_validate(raw)
    except ValidationError as exc:
        raise ProjectPackageError(f"Invalid project manifest: {exc}") from exc


def _migrate_v0_manifest(raw: dict) -> dict:
    migrated = dict(raw)
    migrated["version"] = 1
    project = dict(migrated.get("project") or {})
    source_pdf = dict(migrated.get("source_pdf") or {})
    for old_key, new_key in (
        ("original_filename", "original_filename"),
        ("source_sha256", "sha256"),
        ("source_size_bytes", "size_bytes"),
    ):
        if old_key in project and new_key not in source_pdf:
            source_pdf[new_key] = project.pop(old_key)
    project.setdefault("source", PROJECT_SOURCE_PATH)
    migrated["project"] = project
    migrated["source_pdf"] = source_pdf
    migrated.setdefault("export_preferences", {"format": "all"})
    migrated.setdefault("markups", [])
    for plan in migrated.get("plans", []):
        if "page_index" in plan and "page" not in plan:
            plan["page"] = plan.pop("page_index") + 1
        if "units_per_point" in plan and "units_per_pdf_point" not in plan:
            plan["units_per_pdf_point"] = plan.pop("units_per_point")
        if "exclude_regions" in plan and "exclusion_masks" not in plan:
            plan["exclusion_masks"] = plan.pop("exclude_regions")
    return migrated


def _validate_members(members: list[zipfile.ZipInfo]) -> None:
    if len(members) > MAX_PACKAGE_FILES:
        raise ProjectPackageError("Project package contains too many files.")
    total_size = 0
    seen: set[str] = set()
    for member in members:
        name = member.filename
        if name in seen:
            raise ProjectPackageError(f"Project package contains duplicate entry: {name}")
        seen.add(name)
        path = PurePosixPath(name)
        if (
            not name
            or name.startswith("/")
            or "\\" in name
            or ".." in path.parts
        ):
            raise ProjectPackageError(f"Unsafe project package path: {name!r}")
        mode = member.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise ProjectPackageError(f"Project package contains a symbolic link: {name}")
        if not (
            name in {"manifest.json", PROJECT_SOURCE_PATH}
            or name.startswith("previews/")
            or name.startswith("exports/")
        ):
            raise ProjectPackageError(f"Unsupported project package entry: {name}")
        total_size += member.file_size
        if total_size > MAX_PACKAGE_UNCOMPRESSED_BYTES:
            raise ProjectPackageError("Project package is too large when unpacked.")


def _safe_group_name(group: str, name: str) -> str:
    normalized = PurePosixPath(name)
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or ".." in normalized.parts
        or normalized.name in {"", ".", ".."}
    ):
        raise ProjectPackageError(f"Unsafe {group} package path: {name!r}")
    return f"{group}/{normalized.as_posix()}"


def _write_group(
    bundle: zipfile.ZipFile,
    group: str,
    files: Mapping[str, bytes | Path],
) -> None:
    for name, source in files.items():
        archive_name = _safe_group_name(group, name)
        content = source.read_bytes() if isinstance(source, Path) else source
        bundle.writestr(archive_name, content)


def _read_group(
    bundle: zipfile.ZipFile,
    members: list[zipfile.ZipInfo],
    prefix: str,
) -> dict[str, bytes]:
    return {
        member.filename[len(prefix) :]: bundle.read(member)
        for member in members
        if member.filename.startswith(prefix) and not member.is_dir()
    }
