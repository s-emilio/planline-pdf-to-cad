# Planline — PDF Planset to Editable CAD

Planline is a local web app for extracting editable SVG and CAD linework from
digitally generated vector PDF plansets.

The app:

- detects likely floor-plan regions;
- lets you correct the crop, rotation, units, and drawing scale;
- reads scale labels from native PDF text or optional local OCR;
- exports lines, polylines, curves, fills, and text to editable SVG and DXF R2018;
- optionally converts DXF to DWG 2018 through ODA File Converter;
- optionally builds a physically scaled Blender edge mesh with a packed source plane;
- creates one drawing per calibrated plan plus JSON/HTML reports and previews.

Choose **SVG + CAD**, **SVG only**, **CAD only**, or **SVG + Blender edge mesh**
at export time. SVG and DXF are written from the same normalized drawing model,
so they share the confirmed crop, rotation, scale, units, filters, masks, and
layer organization.

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

## Blender support

Install [Blender](https://www.blender.org/download/) to enable
**SVG + Blender edge mesh**. Planline checks:

1. the `BLENDER_EXECUTABLE` environment variable;
2. `blender` on the command path;
3. `/Applications/Blender.app/Contents/MacOS/Blender`;
4. versioned Blender applications in `/Applications`.

The status indicator includes **Blender ready** when a usable installation is
found. Each Blender export contains:

- the physically scaled source SVG;
- a `.blend` file containing one flat `Plan_Edges` mesh;
- edges only, with mesh faces removed;
- a `PDF_Source_Plane` sized to the full calibrated crop;
- the rendered source PNG packed into the Blender file;
- the source plane 2 mm beneath the edge mesh to prevent z-fighting;
- a visible 180-degree X-axis rotation on the source plane;
- an automatically calculated translation that aligns the full crop with
  Blender's SVG linework center.

The scene uses meters and stores the source units, verified plan dimensions,
alignment offsets, Blender version, vertex count, edge count, and face count as
metadata in the conversion report. This makes the result a practical tracing
base for reconstructing walls and other architecture in 3D.

If Blender is unavailable, the Blender option is disabled in the interface.
Set an explicit executable when needed:

```bash
BLENDER_EXECUTABLE="/path/to/blender" .venv/bin/python -m pdf_plan_to_dwg
```

## Scale

Planline checks native PDF text first and falls back to local OCR when
Tesseract is installed. It recognizes common architectural fractions, decimal
imperial scales, metric ratios, and metric written scales. Detected scale text
is always a proposal rather than an unquestioned measurement.

- When one scale is detected, review it and choose **Confirm scale**. This saves
  the current crop, name, rotation, units, and scale and makes the plan ready
  for export.
- When multiple scales are detected, choose the correct option from
  **Detected scale options**. Selecting an option immediately saves the plan
  and makes it ready for export.
- When detection is wrong or absent, open **Two-point calibration**, click two
  known points, enter their real-world distance, and choose
  **Apply calibration**. Applying calibration immediately saves the plan and
  makes it ready for export.

All CAD model-space output is 1:1, in inches for imperial plans and millimeters
for metric plans.

The plan inspector uses a two-step workflow:

1. **Measurements** contains rotation, units, detected scale options, and
   two-point calibration.
2. After a scale is saved, Measurements folds closed and
   **Cleanup & masks** opens automatically.

Existing calibrated plans open directly on the cleanup step. Measurements can
still be reopened whenever the scale or units need correction.

## Blender cleanup filters

Each plan can optionally remove text and restrict exported geometry to
horizontal and vertical linework:

- **Remove text and outlined words** omits native PDF text and combines local
  Tesseract OCR regions with compact-vector heuristics to mask lettering that
  was converted to outlines. This is best-effort because outlined words are
  ordinary geometry inside the PDF.
- **Keep horizontal and vertical linework only** keeps segments within the
  selected angle tolerance of 0 or 90 degrees. It also removes curves and
  filled hatches, producing simpler linework for Blender reconstruction.

These are intentionally aggressive export filters. Orthogonal mode can remove
real angled walls, stairs, ramps, door swings, and other useful geometry, so
leave it disabled for irregular plans or make a second unfiltered export.

### Exclusion masks

Use **Draw exclusion mask** to drag one or more rectangular red masks over
title blocks, legends, notes, adjacent plans, or other regions that should not
be exported. Masks are saved per plan and can be removed individually.
Linework crossing a mask is trimmed at its boundary rather than discarding the
entire line. Masked text and fills are omitted, and curves crossing a mask are
flattened into clipped editable line segments. Mask coordinates are translated
through the PDF page rotation before clipping, so masks follow the visible
sheet even when the source PDF uses a rotated page box.

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
- Blender conversion requires a local Blender installation with Grease Pencil
  SVG import support.

## Privacy

The default server listens only on `127.0.0.1`. Uploaded PDFs and generated
files stay on the machine running Planline and are stored in its temporary
directory until the job is deleted or the operating system clears temporary
files.

## License

Planline is licensed under AGPL-3.0-or-later. PyMuPDF is dual-licensed under the
AGPL and a commercial license; review its licensing terms before distributing
or operating a modified version where the AGPL is not suitable.
