from __future__ import annotations

import html
import io
import json
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

import ezdxf
import pymupdf
from ezdxf import units
from ezdxf.addons.drawing import Frontend, RenderContext, layout
from ezdxf.addons.drawing.config import BackgroundPolicy, ColorPolicy, Configuration
from ezdxf.addons.drawing.pymupdf import PyMuPdfBackend
from ezdxf.colors import rgb2int
from PIL import Image, ImageDraw, ImageFont

from .drawing_model import DrawingModel, build_drawing_model, unrotated_page_rect
from .models import PlanRegion


def _add_layer(doc: ezdxf.document.Drawing, name: str, color: tuple | None = None) -> None:
    if name in doc.layers:
        return
    layer = doc.layers.add(name=name, color=7)
    if color:
        rgb = tuple(max(0, min(255, round(value * 255))) for value in color[:3])
        layer.rgb = rgb


def _hex_rgb(value: str) -> tuple[int, int, int]:
    clean = value.lstrip("#")
    return tuple(int(clean[index:index + 2], 16) for index in (0, 2, 4))


def _entity_attribs(layer: str, color: str) -> dict[str, Any]:
    return {"layer": layer, "true_color": rgb2int(_hex_rgb(color))}


def write_dxf(model: DrawingModel, output_path: Path) -> dict[str, int]:
    doc = ezdxf.new("R2018", setup=True)
    doc.units = units.IN if model.units == "in" else units.MM
    doc.header["$EXTMIN"] = (0, 0, 0)
    doc.header["$EXTMAX"] = (model.width, model.height, 0)
    modelspace = doc.modelspace()
    entity_counts: Counter[str] = Counter()
    layer_colors: dict[str, str] = {}
    for entity in (*model.polylines, *model.curves):
        layer_colors.setdefault(entity.layer, entity.style.color)
    for entity in model.polygons:
        layer_colors.setdefault(entity.layer, entity.fill)
    for entity in model.texts:
        layer_colors.setdefault(entity.layer, entity.color)
    for layer_name, color in layer_colors.items():
        _add_layer(doc, layer_name, tuple(component / 255 for component in _hex_rgb(color)))

    for polyline in model.polylines:
        attributes = _entity_attribs(polyline.layer, polyline.style.color)
        if len(polyline.points) == 2:
            modelspace.add_line(polyline.points[0], polyline.points[1], dxfattribs=attributes)
            entity_counts["LINE"] += 1
        else:
            modelspace.add_lwpolyline(polyline.points, dxfattribs=attributes)
            entity_counts["LWPOLYLINE"] += 1
    for curve in model.curves:
        modelspace.add_open_spline(
            control_points=curve.points,
            degree=3,
            dxfattribs=_entity_attribs(curve.layer, curve.style.color),
        )
        entity_counts["SPLINE"] += 1
    for polygon in model.polygons:
        hatch = modelspace.add_hatch(
            color=7,
            dxfattribs=_entity_attribs(polygon.layer, polygon.fill),
        )
        hatch.paths.add_polyline_path(polygon.points, is_closed=True)
        entity_counts["HATCH"] += 1
    for text in model.texts:
        entity = modelspace.add_text(
            text.text,
            height=text.height,
            rotation=text.rotation,
            dxfattribs=_entity_attribs(text.layer, text.color),
        )
        entity.dxf.insert = text.insert
        entity_counts["TEXT"] += 1
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(output_path)
    return dict(entity_counts)


def _number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def _svg_point(model: DrawingModel, point: tuple[float, float]) -> tuple[float, float]:
    return point[0], model.height - point[1]


def _svg_layer_id(name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", name.strip()).strip("-")
    return value or "layer"


def _svg_stroke_attributes(style) -> dict[str, str]:
    attributes = {
        "fill": "none",
        "stroke": style.color,
        "stroke-width": _number(style.width),
        "stroke-opacity": _number(style.opacity),
        "stroke-linecap": style.linecap,
        "stroke-linejoin": style.linejoin,
    }
    if style.dasharray:
        attributes["stroke-dasharray"] = " ".join(_number(value) for value in style.dasharray)
    return attributes


def write_svg(model: DrawingModel, output_path: Path) -> None:
    namespace = "http://www.w3.org/2000/svg"
    ET.register_namespace("", namespace)
    root = ET.Element(
        f"{{{namespace}}}svg",
        {
            "version": "1.1",
            "viewBox": f"0 0 {_number(model.width)} {_number(model.height)}",
            "width": f"{_number(model.width)}{model.units}",
            "height": f"{_number(model.height)}{model.units}",
        },
    )
    metadata = {
        "generator": "Planline PDF to CAD",
        "source_page": model.source_page,
        "crop": model.crop.model_dump(),
        "rotation": model.rotation,
        "units": model.units,
        "units_per_pdf_point": model.units_per_point,
        "scale_label": model.scale_label,
        "filters": {
            "remove_text": model.remove_text,
            "orthogonal_only": model.orthogonal_only,
            "angle_tolerance_degrees": model.angle_tolerance,
            "exclude_regions": [
                region.model_dump() for region in model.exclude_regions
            ],
        },
    }
    ET.SubElement(root, f"{{{namespace}}}metadata").text = json.dumps(metadata)
    entities_by_layer: dict[str, list[tuple[str, Any]]] = defaultdict(list)
    for entity in model.polylines:
        entities_by_layer[entity.layer].append(("polyline", entity))
    for entity in model.curves:
        entities_by_layer[entity.layer].append(("curve", entity))
    for entity in model.polygons:
        entities_by_layer[entity.layer].append(("polygon", entity))
    for entity in model.texts:
        entities_by_layer[entity.layer].append(("text", entity))

    used_ids: Counter[str] = Counter()
    for layer_name, entities in entities_by_layer.items():
        base_id = _svg_layer_id(layer_name)
        used_ids[base_id] += 1
        layer_id = base_id if used_ids[base_id] == 1 else f"{base_id}-{used_ids[base_id]}"
        group = ET.SubElement(
            root,
            f"{{{namespace}}}g",
            {"id": layer_id, "data-layer": layer_name},
        )
        for kind, entity in entities:
            if kind == "polyline":
                points = [_svg_point(model, point) for point in entity.points]
                command = "M " + " L ".join(
                    f"{_number(x)} {_number(y)}" for x, y in points
                )
                ET.SubElement(
                    group,
                    f"{{{namespace}}}path",
                    {"d": command, **_svg_stroke_attributes(entity.style)},
                )
            elif kind == "curve":
                points = [_svg_point(model, point) for point in entity.points]
                p0, p1, p2, p3 = points
                command = (
                    f"M {_number(p0[0])} {_number(p0[1])} "
                    f"C {_number(p1[0])} {_number(p1[1])} "
                    f"{_number(p2[0])} {_number(p2[1])} "
                    f"{_number(p3[0])} {_number(p3[1])}"
                )
                ET.SubElement(
                    group,
                    f"{{{namespace}}}path",
                    {"d": command, **_svg_stroke_attributes(entity.style)},
                )
            elif kind == "polygon":
                points = [_svg_point(model, point) for point in entity.points]
                ET.SubElement(
                    group,
                    f"{{{namespace}}}polygon",
                    {
                        "points": " ".join(f"{_number(x)},{_number(y)}" for x, y in points),
                        "fill": entity.fill,
                        "fill-opacity": _number(entity.opacity),
                        "stroke": "none",
                    },
                )
            else:
                x, y = _svg_point(model, entity.insert)
                attributes = {
                    "x": _number(x),
                    "y": _number(y),
                    "fill": entity.color,
                    "fill-opacity": _number(entity.opacity),
                    "font-size": _number(entity.height),
                    "font-family": entity.font_family,
                }
                if entity.rotation:
                    attributes["transform"] = (
                        f"rotate({_number(-entity.rotation)} {_number(x)} {_number(y)})"
                    )
                ET.SubElement(group, f"{{{namespace}}}text", attributes).text = entity.text
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(output_path, encoding="utf-8", xml_declaration=True)


def report_for_model(
    model: DrawingModel,
    dxf_path: Path | None,
    svg_path: Path | None,
    dxf_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    filtered_counts = {
        key: value
        for key, value in model.unsupported.items()
        if key.startswith("filtered_")
    }
    unsupported = {
        key: value
        for key, value in model.unsupported.items()
        if not key.startswith("filtered_")
    }
    return {
        "plan_id": None,
        "name": model.name,
        "source_page": model.source_page,
        "crop": model.crop.model_dump(),
        "rotation": model.rotation,
        "units": model.units,
        "units_per_pdf_point": model.units_per_point,
        "scale_label": model.scale_label,
        "filters": {
            "remove_text": model.remove_text,
            "orthogonal_only": model.orthogonal_only,
            "angle_tolerance_degrees": model.angle_tolerance,
            "exclude_regions": [
                region.model_dump() for region in model.exclude_regions
            ],
        },
        "confidence": model.confidence,
        "drawing_extents": {"width": model.width, "height": model.height},
        "entity_counts": dxf_counts or model.entity_counts,
        "shared_model_counts": model.entity_counts,
        "layer_counts": model.layer_counts,
        "filtered_geometry": filtered_counts,
        "unsupported": unsupported,
        "warnings": model.warnings,
        "dxf": dxf_path.name if dxf_path else None,
        "svg": svg_path.name if svg_path else None,
        "dwg": None,
    }


def convert_plan_to_dxf(
    pdf_path: Path,
    plan: PlanRegion,
    output_path: Path,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    model = build_drawing_model(pdf_path, plan, progress=progress)
    if progress:
        progress(
            "Writing DXF with "
            + f"{sum(model.entity_counts.values()):,} shared-model entities "
            + f"on {len(model.layer_counts):,} layers"
        )
    counts = write_dxf(model, output_path)
    report = report_for_model(model, output_path, None, counts)
    report["plan_id"] = plan.id
    return report


def find_oda_converter() -> Path | None:
    override = os.environ.get("ODA_FILE_CONVERTER")
    candidates = [
        override,
        shutil.which("ODAFileConverter"),
        "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter",
        "/Applications/ODA File Converter.app/Contents/MacOS/ODAFileConverter",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def find_blender() -> Path | None:
    override = os.environ.get("BLENDER_EXECUTABLE")
    candidates = [
        override,
        shutil.which("blender"),
        "/Applications/Blender.app/Contents/MacOS/Blender",
    ]
    candidates.extend(
        str(path)
        for path in sorted(
            Path("/Applications").glob("Blender *.app/Contents/MacOS/Blender"),
            reverse=True,
        )
    )
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def convert_svg_to_blend(
    svg_path: Path,
    source_png: Path,
    output_path: Path,
    model: DrawingModel,
) -> tuple[Path | None, str | None, dict[str, Any] | None]:
    blender = find_blender()
    if blender is None:
        return (
            None,
            "Blender is not installed; the physically scaled SVG was preserved.",
            None,
        )
    script = Path(__file__).with_name("blender_import.py")
    width_m = model.width * (0.0254 if model.units == "in" else 0.001)
    height_m = model.height * (0.0254 if model.units == "in" else 0.001)
    points = [
        point
        for entity in (*model.polylines, *model.curves, *model.polygons)
        for point in entity.points
    ]
    points.extend(text.insert for text in model.texts)
    if points:
        content_center_x = (
            min(point[0] for point in points) + max(point[0] for point in points)
        ) / 2
        content_center_y = (
            min(point[1] for point in points) + max(point[1] for point in points)
        ) / 2
    else:
        content_center_x = model.width / 2
        content_center_y = model.height / 2
    meters_per_unit = 0.0254 if model.units == "in" else 0.001
    plane_offset_x_m = (model.width / 2 - content_center_x) * meters_per_unit
    plane_offset_y_m = (model.height / 2 - content_center_y) * meters_per_unit
    command = [
        str(blender),
        "--background",
        "--factory-startup",
        "--python",
        str(script),
        "--",
        str(svg_path),
        str(source_png),
        str(output_path),
        str(width_m),
        str(height_m),
        str(plane_offset_x_m),
        str(plane_offset_y_m),
        model.name,
        model.units,
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"Blender conversion could not run: {exc}", None
    marker = "PLANLINE_BLEND_STATS:"
    stats = None
    for line in result.stdout.splitlines():
        if line.startswith(marker):
            stats = json.loads(line[len(marker) :])
    if result.returncode != 0 or not output_path.is_file() or stats is None:
        detail = (result.stderr or result.stdout or "unknown Blender error").strip()
        return None, f"Blender conversion failed: {detail[-1000:]}", None
    stats["blender_executable"] = str(blender)
    return output_path, None, stats


def convert_dxf_to_dwg(dxf_path: Path, output_dir: Path) -> tuple[Path | None, str | None]:
    converter = find_oda_converter()
    if converter is None:
        return None, "ODA File Converter is not installed; DXF was preserved."
    oda_input = output_dir / "_oda_input"
    oda_output = output_dir / "_oda_output"
    oda_input.mkdir(exist_ok=True)
    oda_output.mkdir(exist_ok=True)
    staged = oda_input / dxf_path.name
    shutil.copy2(dxf_path, staged)
    command = [
        str(converter),
        str(oda_input),
        str(oda_output),
        "ACAD2018",
        "DWG",
        "0",
        "1",
        "*.dxf",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"ODA conversion could not run: {exc}"
    expected = oda_output / f"{dxf_path.stem}.dwg"
    if result.returncode != 0 or not expected.exists():
        detail = (result.stderr or result.stdout or "unknown ODA error").strip()
        return None, f"ODA conversion failed: {detail[:500]}"
    destination = output_dir / expected.name
    shutil.copy2(expected, destination)
    shutil.rmtree(oda_input, ignore_errors=True)
    shutil.rmtree(oda_output, ignore_errors=True)
    return destination, None


def render_source_crop(pdf_path: Path, plan: PlanRegion, output_path: Path) -> None:
    document = pymupdf.open(pdf_path)
    try:
        page = document[plan.page_index]
        page_rotation = page.rotation
        crop = unrotated_page_rect(page, plan.crop.normalized())
        page.set_rotation(0)
        pixmap = page.get_pixmap(
            matrix=pymupdf.Matrix(2, 2),
            clip=pymupdf.Rect(crop.x0, crop.y0, crop.x1, crop.y1),
            alpha=False,
        )
        pixmap.save(output_path)
        preview_rotation = (plan.rotation - page_rotation) % 360
        if preview_rotation:
            _rotate_preview(output_path, preview_rotation)
    finally:
        document.close()


def _rotate_preview(output_path: Path, rotation: int) -> None:
    transpose = {
        90: Image.Transpose.ROTATE_90,
        180: Image.Transpose.ROTATE_180,
        270: Image.Transpose.ROTATE_270,
    }.get(rotation % 360)
    if transpose is None:
        return
    with Image.open(output_path) as image:
        image.transpose(transpose).save(output_path, "PNG")


def render_dxf(
    dxf_path: Path,
    output_path: Path,
    preview_rotation: int = 0,
) -> None:
    document = ezdxf.readfile(dxf_path)
    backend = PyMuPdfBackend()
    context = RenderContext(document)
    configuration = Configuration(
        background_policy=BackgroundPolicy.WHITE,
        color_policy=ColorPolicy.COLOR,
    )
    Frontend(context, backend, config=configuration).draw_layout(
        document.modelspace(), finalize=True
    )
    page = layout.Page(0, 0, max_width=300, max_height=300)
    image_bytes = backend.get_pixmap_bytes(page, dpi=144, alpha=False)
    if preview_rotation % 360:
        with Image.open(io.BytesIO(image_bytes)) as image:
            transpose = {
                90: Image.Transpose.ROTATE_90,
                180: Image.Transpose.ROTATE_180,
                270: Image.Transpose.ROTATE_270,
            }[preview_rotation % 360]
            image.transpose(transpose).save(output_path, "PNG")
    else:
        output_path.write_bytes(image_bytes)


def make_comparison(source_path: Path, cad_path: Path, output_path: Path) -> None:
    with Image.open(source_path).convert("RGB") as source, Image.open(cad_path).convert("RGB") as cad:
        target_height = max(source.height, cad.height, 500)

        def resized(image: Image.Image) -> Image.Image:
            ratio = target_height / image.height
            return image.resize((max(1, round(image.width * ratio)), target_height))

        source_scaled, cad_scaled = resized(source), resized(cad)
        margin = 20
        header = 76
        label_font = ImageFont.load_default(size=22)
        source_panel_width = source_scaled.width + (margin * 2)
        cad_panel_width = cad_scaled.width + (margin * 2)
        canvas = Image.new(
            "RGB",
            (
                source_panel_width + cad_panel_width,
                target_height + header + (margin * 2),
            ),
            "white",
        )
        source_x = margin
        cad_x = source_panel_width + margin
        image_y = header + margin
        canvas.paste(source_scaled, (source_x, image_y))
        canvas.paste(cad_scaled, (cad_x, image_y))
        draw = ImageDraw.Draw(canvas)
        draw.rectangle((0, 0, canvas.width, header), fill="#102019")
        label_y = (header - 22) // 2
        draw.text((margin, label_y), "PDF SOURCE", fill="white", font=label_font)
        draw.text(
            (source_panel_width + margin, label_y),
            "CAD OUTPUT",
            fill="white",
            font=label_font,
        )
        canvas.save(output_path, "PNG")


def write_reports(output_dir: Path, job_name: str, reports: list[dict], warnings: list[str]) -> None:
    payload = {"source": job_name, "plans": reports, "warnings": warnings}
    (output_dir / "conversion-report.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    rows = []
    for report in reports:
        counts = ", ".join(f"{key}: {value}" for key, value in report["entity_counts"].items())
        files = ", ".join(
            value
            for key in ("svg", "dxf", "dwg", "blend")
            if (value := report.get(key))
        )
        plan_warnings = "<br>".join(html.escape(item) for item in report["warnings"]) or "None"
        rows.append(
            "<tr>"
            f"<td>{html.escape(report['name'])}</td>"
            f"<td>{report['source_page']}</td>"
            f"<td>{html.escape(report['scale_label'] or 'manual')}</td>"
            f"<td>{html.escape(report['units'])}</td>"
            f"<td>{html.escape(files)}</td>"
            f"<td>{html.escape(counts)}</td>"
            f"<td>{plan_warnings}</td>"
            "</tr>"
        )
    warning_html = "".join(f"<li>{html.escape(item)}</li>" for item in warnings)
    document = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Conversion report</title>
<style>
body{{font:14px system-ui;margin:32px;color:#142019}} table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #ccd6d0;padding:9px;text-align:left;vertical-align:top}}
th{{background:#edf3ef}} h1{{font-size:24px}}
</style></head><body>
<h1>PDF planset conversion report</h1>
<p>Source: {html.escape(job_name)}</p>
<ul>{warning_html}</ul>
<table><thead><tr><th>Plan</th><th>Page</th><th>Scale</th><th>Units</th><th>Files</th>
<th>Entities</th><th>Warnings</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
</body></html>"""
    (output_dir / "conversion-report.html").write_text(document, encoding="utf-8")
