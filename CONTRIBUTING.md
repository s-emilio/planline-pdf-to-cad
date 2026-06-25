# Contributing

## Development setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pytest
.venv/bin/python -m pdf_plan_to_dwg
```

The app opens at <http://127.0.0.1:8765>.

## Pull requests

- Keep processing local by default.
- Add tests for PDF producer-specific edge cases.
- Do not silently trace raster-only pages.
- Preserve the confirmed crop, rotation, scale, and units across SVG and CAD.
- Avoid committing plansets unless they are clearly redistributable fixtures.

## Licensing

Contributions are accepted under AGPL-3.0-or-later. PyMuPDF is available under
AGPL or a commercial license; downstream distributors are responsible for
confirming that their use complies with the applicable licenses.

