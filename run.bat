@echo off
REM Start SmartPark on http://127.0.0.1:8000  (Windows)
REM The window stays open if anything goes wrong, so the error can be read.
cd /d "%~dp0"

REM ---- 1. Find Python (the "py" launcher first, then "python") ----
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY (
  python --version >nul 2>nul && set "PY=python"
)
if not defined PY goto nopython

REM ---- 2. Create the virtual environment if it is missing or broken ----
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  if exist ".venv" rmdir /s /q ".venv"
  %PY% -m venv .venv
  if errorlevel 1 goto failed
)

REM ---- 3. Install the packages if they are not all there ----
".venv\Scripts\python.exe" -c "import fastapi, uvicorn, sqlalchemy, sklearn, bcrypt, qrcode" >nul 2>nul
if errorlevel 1 (
  echo Installing packages - this takes a few minutes the first time...
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto failed
)

REM ---- 4. Start the server ----
echo.
echo Starting SmartPark. Open http://127.0.0.1:8000 in your browser.
echo Keep this window open while you use the app. Press Ctrl+C to stop.
echo.
".venv\Scripts\python.exe" -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
echo.
echo SmartPark has stopped.
pause
exit /b 0

:nopython
echo.
echo Python was not found.
echo Install Python 3.12 from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" on the first screen of the installer.
echo Then run this file again.
echo.
pause
exit /b 1

:failed
echo.
echo Something went wrong - see the messages above.
echo Take a screenshot of this window.
echo.
pause
exit /b 1
