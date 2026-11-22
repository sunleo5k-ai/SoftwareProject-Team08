"""SmartPark - AI-Powered Smart City Parking Network (SWE 401 course project).

Run:  uvicorn backend.app.main:app --reload     (from the project root)
API docs (OpenAPI, NFR-19): http://127.0.0.1:8000/docs
"""
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import clock
from .database import SessionLocal
from .routers import admin, auth, driver, gate
from .seed import seed

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
JOB_INTERVAL_SECONDS = int(os.getenv("JOB_INTERVAL_SECONDS", "30"))
_stop = threading.Event()


def _scheduler_loop():
    """ReservationScheduler: expires holds, releases no-shows, sends reminders,
    purges old plate reads and runs scheduled model retraining."""
    while not _stop.wait(JOB_INTERVAL_SECONDS):
        db = SessionLocal()
        try:
            admin.run_jobs(db)
        except Exception as exc:              # keep the loop alive; log and continue
            print(f"[scheduler] {clock.now()} error: {exc}")
        finally:
            db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    seed()
    t = None
    if os.getenv("DISABLE_SCHEDULER") != "1":
        t = threading.Thread(target=_scheduler_loop, daemon=True)
        t.start()
    yield
    _stop.set()


app = FastAPI(title="SmartPark API", version="1.0.0", lifespan=lifespan,
              description="AI-powered smart city parking network - SWE 401 Fall 2026")
app.include_router(auth.router)
app.include_router(driver.router)
app.include_router(gate.router)
app.include_router(admin.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "time": clock.now().isoformat()}


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")
