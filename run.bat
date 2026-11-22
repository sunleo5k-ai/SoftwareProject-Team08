@echo off
REM Start SmartPark on http://127.0.0.1:8000  (Windows)
cd /d %~dp0
if not exist .venv (
  python -m venv .venv
  .venv\Scripts\pip install -r requirements.txt
)
.venv\Scripts\python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
