#!/usr/bin/env bash
# Start SmartPark on http://127.0.0.1:8000  (macOS / Linux)
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi
.venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
