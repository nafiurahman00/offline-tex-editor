#!/usr/bin/env bash
# Start the editor from a source checkout (no installation needed).
# Usage: ./start.sh [--root PROJECT_DIR] [--main FILE] [--engine auto|pdflatex|xelatex|lualatex] [--open] ...
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$here${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m offline_tex_editor "$@"
