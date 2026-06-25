#!/bin/zsh
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$ROOT/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  echo "Creating the Planline virtual environment..."
  python3 -m venv "$ROOT/.venv"
fi

echo "Installing or updating Planline..."
"$PYTHON" -m pip install --quiet --upgrade pip
"$PYTHON" -m pip install --quiet --editable "$ROOT"

cd "$ROOT"
exec "$PYTHON" -m pdf_plan_to_dwg

