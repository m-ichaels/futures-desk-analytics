#!/usr/bin/env bash
# Full pipeline: data -> build the C++ replay -> run -> figures -> summary -> report -> notebook -> tests.
# Usage: scripts/run_all.sh [--skip-download] [--fast]
set -euo pipefail
cd "$(dirname "$0")/.."
[[ " $* " == *" --skip-download "* ]] || python tools/download.py
python build_ext.py build_ext --inplace || echo "C++ extension not built (the queue study needs it); see build.ps1 on Windows"
if [[ " $* " == *" --fast "* ]]; then python -m futdesk run --fast; else python -m futdesk run; fi
python scripts/plots.py
python scripts/summarize.py
python scripts/report.py
python scripts/make_notebook.py --execute
python -m pytest
echo done
