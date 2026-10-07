#!/usr/bin/env bash
# Single-command start for the Event Sync Service.
#
# Creates a virtualenv on first run, installs dependencies, then serves the app.
set -euo pipefail

cd "$(dirname "$0")"

VENV=".venv"
PYTHON="${PYTHON:-python3}"

if [ ! -d "$VENV" ]; then
  echo "Creating virtualenv..."
  "$PYTHON" -m venv "$VENV"
fi

echo "Installing dependencies..."
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r requirements.txt

echo
echo "Event Sync Service running at http://127.0.0.1:8000"
echo "API docs at http://127.0.0.1:8000/docs"
echo
exec "$VENV/bin/uvicorn" app.main:app --host 127.0.0.1 --port 8000 "$@"
