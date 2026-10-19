#!/bin/sh
# Stable interpreter and script paths; never auto-install or modify private data.
set -eu
APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
    echo "Falta .venv. Sigue la instalación breve en docs/local.md."
    exit 2
fi
exec "$APP_DIR/.venv/bin/python" "$APP_DIR/madoka_local.py" "$@"
