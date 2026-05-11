#!/usr/bin/env bash
# Convenience launcher. Creates a venv on first run, installs deps,
# and starts the app. Run again on subsequent uses.
set -e

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "First run: creating virtualenv and installing dependencies..."
  python3 -m venv .venv
  ./.venv/bin/pip install --upgrade pip
  ./.venv/bin/pip install -r requirements.txt
fi

exec ./.venv/bin/python app.py
