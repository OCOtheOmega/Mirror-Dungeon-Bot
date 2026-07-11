#!/usr/bin/env bash
set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

OUT_PARENT="/output"
OUT_DIR="$OUT_PARENT/app"
NUITKA_OUT_DIR="$ROOT_DIR/build/nuitka-linux"
NUITKA_DIST_DIR=""

if [ "$INSIDE_DOCKER" = "1" ]; then
    echo "Building executable inside Docker with Nuitka..."

    rm -rf "$OUT_DIR" "$NUITKA_OUT_DIR"
    mkdir -p "$OUT_PARENT" "$NUITKA_OUT_DIR"

    python -m nuitka \
      --standalone \
      --enable-plugin=pyside6 \
      --assume-yes-for-downloads \
      --nofollow-import-to=source.utils.os_windows_backend \
      --output-dir="$NUITKA_OUT_DIR" \
      --output-filename=app \
      --include-data-dir="$ROOT_DIR/ImageAssets/UI"=ImageAssets/UI \
      --include-data-dir="$ROOT_DIR/ImageAssets/AppUI"=ImageAssets/AppUI \
      --include-data-files="$ROOT_DIR/ImageAssets/app.png"=ImageAssets/app.png \
      --include-data-files="$ROOT_DIR/version"=version \
      --include-data-files="$ROOT_DIR/source/utils/movement/model.npz"=move_assets/model.npz \
      "$ROOT_DIR/App.py"

    NUITKA_DIST_DIR="$(find "$NUITKA_OUT_DIR" -maxdepth 1 -mindepth 1 -type d -name '*.dist' | head -n 1)"
    if [ -z "$NUITKA_DIST_DIR" ]; then
      echo "ERROR: Nuitka dist directory was not found in $NUITKA_OUT_DIR"
      exit 1
    fi

    # Not bundled by Nuitka for some reason
    XCB_CURSOR_LIB="$(find /usr/lib64 /lib64 /usr/lib /lib -maxdepth 2 -type f -name 'libxcb-cursor.so.0*' 2>/dev/null | head -n 1)"
    if [ -z "$XCB_CURSOR_LIB" ]; then
      echo "ERROR: libxcb-cursor.so.0 was not found in the Docker image"
      exit 1
    fi
    cp -a "$XCB_CURSOR_LIB" "$NUITKA_DIST_DIR/"

    cp -a "$NUITKA_DIST_DIR" "$OUT_DIR"
    chmod +x "$OUT_DIR/app"
    echo "Nuitka output prepared at $OUT_DIR"
else
    echo "This script is intended to run in Docker with INSIDE_DOCKER=1."
    exit 1
fi
