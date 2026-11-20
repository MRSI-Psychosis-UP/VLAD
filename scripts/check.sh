#!/usr/bin/env bash
set -euo pipefail
VLAD_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$VLAD_ROOT"
PYTHON_BIN="${VLAD_PYTHON_BIN:-${VLAD_ROOT}/.venv/bin/python}"
[[ -x "$PYTHON_BIN" ]] || PYTHON_BIN="$(command -v python3)"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="${VLAD_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
for script in ./vlad ./install.sh workflows/*.sh scripts/*.sh scripts/compat/*.sh docker/*.sh; do
    bash -n "$script"
done
"$PYTHON_BIN" - <<'PY'
import ast
from pathlib import Path
for directory in (Path('src'), Path('scripts'), Path('tests')):
    for path in directory.rglob('*.py'):
        ast.parse(path.read_text(), filename=str(path))
print('Python and Bash syntax checks passed.')
PY
"$PYTHON_BIN" -m unittest discover -s tests -v
