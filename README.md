# Planline — PDF Planset to Editable CAD

Planline is a local web app for extracting editable SVG and CAD linework from
digitally generated vector PDF plansets.

The app:

- detects likely floor-plan regions;
- lets you correct the crop, rotation, units, and drawing scale;
- exports lines, polylines, curves, fills, and text to editable SVG and DXF R2018;
- optionally converts DXF to DWG 2018 through ODA File Converter;
- creates one drawing per confirmed plan plus JSON/HTML reports and previews.

Choose **SVG + CAD**, **SVG only**, or **CAD only** at export time. SVG and DXF
are written from the same normalized drawing model, so they share the confirmed
crop, rotation, scale, units, and layer organization.

It does **not** trace scanned drawings or infer intelligent wall, door, or BIM
objects.

## Install

Requires Python 3.12 or newer.

```bash
git clone https://github.com/s-emilio/planline-pdf-to-cad.git
cd planline-pdf-to-cad
python3 -m venv .venv
.venv/bin/python -m pip install --editable .
```

## Run

On macOS, double-click `Launch Planline.command`.

On any supported platform:

```bash
.venv/bin/python -m pdf_plan_to_dwg
```

Open <http://127.0.0.1:8765>.

Useful options:

```bash
.venv/bin/python -m pdf_plan_to_dwg --no-browser
.venv/bin/python -m pdf_plan_to_dwg --host 127.0.0.1 --port 9000
```

## Development

```bash
.venv/bin/python -m pip install --editable ".[dev]"
.venv/bin/python -m pytest
```

## DWG support

DXF export works without proprietary software. For DWG output, install
[ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter).
The app checks common macOS installation paths and the `ODA_FILE_CONVERTER`
environment variable.

If ODA is unavailable or conversion fails, the export still includes the DXF
and a warning in the report.

## Scale

Detected scale text is only a proposal. Every plan must be confirmed before
export. If detection is wrong or absent:

1. choose **Two-point calibration**;
2. click two known points on the preview;
3. enter their real-world distance and units;
4. apply and confirm the plan.

All CAD model-space output is 1:1, in inches for imperial plans and millimeters
for metric plans.

## Limitations

- PDF line types and fonts are approximated when no direct CAD equivalent
  exists.
- Clipped Bézier curves are flattened to line segments; complete curves remain
  editable splines.
- PDF optional-content names are preserved where PyMuPDF exposes them.
- Raster-only sheets are rejected rather than silently producing an empty CAD
  file.

## Privacy

The default server listens only on `127.0.0.1`. Uploaded PDFs and generated
files stay on the machine running Planline and are stored in its temporary
directory until the job is deleted or the operating system clears temporary
files.

## License

Planline is licensed under AGPL-3.0-or-later. PyMuPDF is dual-licensed under the
AGPL and a commercial license; review its licensing terms before distributing
or operating a modified version where the AGPL is not suitable.
