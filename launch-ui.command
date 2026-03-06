#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -f ".venv/bin/python" ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate

if ! python -c "import lms_migration" >/dev/null 2>&1; then
  pip install -e .
fi

python -m lms_migration.ui
