# Planline — PDF Planset to Editable CAD

Planline is a local web app for extracting editable SVG and CAD linework from
digitally generated vector PDF plansets.

The app:

- detects likely floor-plan regions;
- lets you correct the crop, rotation, units, and drawing scale;
- reads scale labels from native PDF text or optional local OCR;
- exports lines, polylines, curves, fills, and text to editable SVG and DXF R2018;
- optionally converts DXF to DWG 2018 through ODA File Converter;
- creates one drawing per calibrated plan plus JSON/HTML reports and previews.

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

For harder scale labels - including PDFs whose text was converted to vector
outlines - install the optional Tesseract OCR engine before launching Planline:

```bash
brew install tesseract
```

On Linux, install the distribution's `tesseract-ocr` package. Restart Planline
after installation. The status indicator will include **OCR ready** when it is
available. Planline still runs without Tesseract, but scale detection will be
limited to extractable PDF text.

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

Planline checks native PDF text first and falls back to local OCR when
Tesseract is installed. It recognizes common architectural fractions, decimal
imperial scales, metric ratios, and metric written scales. Detected scale text
is always a proposal rather than an unquestioned measurement.

- When one scale is detected, review it and choose **Confirm scale**. This saves
  the current crop, name, rotation, units, and scale and makes the plan ready
  for export.
- When multiple scales are detected, choose the correct option from
  **Detected scale options**, then confirm it.
- When detection is wrong or absent, open **Two-point calibration**, click two
  known points, enter their real-world distance, and choose
  **Apply calibration**. Applying calibration immediately saves the plan and
  makes it ready for export.

All CAD model-space output is 1:1, in inches for imperial plans and millimeters
for metric plans.

## Limitations

- PDF line types and fonts are approximated when no direct CAD equivalent
  exists.
- Clipped Bézier curves are flattened to line segments; complete curves remain
  editable splines.
- PDF optional-content names are preserved where PyMuPDF exposes them.
- OCR is used only for scale-label detection; it does not convert raster plans
  or outlined plan annotations into CAD text.
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
