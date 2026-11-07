#!/usr/bin/env bash
# Linux launcher (counterpart of RGBMoCap.cmd) using the developer venvs from tools/setup_linux.py.
set -u
cd "$(dirname "$(readlink -f "$0")")"
if [ ! -x .venv/bin/python ]; then
  echo "RGB MoCap is not installed. Run: python3 tools/setup_linux.py (see docs/LINUX.md)" >&2; exit 1
fi
unset PYTHONHOME PYTHONPATH
export RGB_MOCAP_DATA="$PWD" HF_HOME="$PWD/cache/huggingface" TORCH_HOME="$PWD/cache/torch" \
       PYTHONDONTWRITEBYTECODE=1 RGBMOCAP_BLENDER="$PWD/runtime_character/blender"
# PySide6's QOpenGLWidget is most reliable on XWayland.
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"
exec .venv/bin/python -X utf8 -u -B app.py "$@"
