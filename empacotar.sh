#!/usr/bin/env bash
set -euo pipefail
pasta="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHONDONTWRITEBYTECODE=1 python3 "$pasta/tests/test_core.py"
python3 - "$pasta" <<'PY'
import ast
from pathlib import Path
import sys
root = Path(sys.argv[1])
for path in [root / 'instalar.py', *root.joinpath('extension').rglob('*.py')]:
    ast.parse(path.read_text(), filename=str(path))
print('Sintaxe Python validada.')
PY
# Do not distribute interpreter caches generated while developing the app.
find "$pasta/extension" -type d -name __pycache__ -prune -exec rm -rf -- {} +
mkdir -p "$pasta/dist"
gnome-extensions pack --force --extra-source=monitor.py --extra-source=global_monitor --extra-source=icons \
    --out-dir="$pasta/dist" "$pasta/extension"
echo "Pacote pronto: $pasta/dist/utils-global-monitor@gezer.local.shell-extension.zip"
