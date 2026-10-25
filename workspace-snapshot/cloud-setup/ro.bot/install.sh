#!/usr/bin/env bash
set -euo pipefail

cd /workspace/ro.bot
export UV_CACHE_DIR=/workspace/.cache/uv
export UV_PYTHON_INSTALL_DIR=/workspace/.local/share/uv/python
export UV_SYSTEM_CERTS=true

# Keep the existing checkout and all tracked files unchanged.
if [ ! -x .venv/bin/python ]; then
  uv venv --managed-python --python 3.13.16 .venv
fi
.venv/bin/python -c 'import sys; assert sys.version_info[:2] == (3, 13), "Expected Python 3.13 in .venv"'
# There is no repository lockfile. Use the declared optional serial extra.
uv pip install --python .venv/bin/python --editable '.[serial]'
uv pip check --python .venv/bin/python
.venv/bin/ro --version

# The mapper expects a parent directory containing projects.
# This additive setup preserves other roots and all engine preferences.
.venv/bin/ro setup roots add /workspace
.venv/bin/ro map
.venv/bin/python - <<'PY'
import json
from pathlib import Path
data = json.loads(Path('~/.ro/map.json').expanduser().read_text())
assert any(p['path'] == '/workspace/ro.bot' for p in data['projects']), 'ro.bot not found in local map'
print('Local project map verified')
PY
