"""Application settings, read from environment variables (see .env.example)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]

# Load settings from a .env file in the project root, if there is one (see .env.example).
_env = BASE_DIR / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
DATA_DIR = Path(os.getenv("SMARTPARK_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'smartpark.db'}")
DEMO_MODE = os.getenv("DEMO_MODE", "true").lower() == "true"

# Security (FR-02, NFR-09)
SESSION_IDLE_MINUTES = int(os.getenv("SESSION_IDLE_MINUTES", "15"))
MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15

# Reservation business rules (UC-04)
HOLD_MINUTES = 5
MAX_ACTIVE_RESERVATIONS = 3
MAX_DURATION_HOURS = 24
MIN_LEAD_MINUTES = 10
EARLY_ARRIVAL_MINUTES = 15
MAX_PAYMENT_ATTEMPTS = 3

# Pricing (Specification 2)
VAT_RATE = 0.05
SERVICE_FEE_AED = 1.00
PLATFORM_MAX_MULTIPLIER = 2.0

# Gate access (Specification 3, NFR-11)
PLATE_CONFIDENCE_THRESHOLD = 0.90
PLATE_RETENTION_DAYS = 30

# Forecast model file (FR-17, FR-19)
MODEL_PATH = DATA_DIR / "forecast_model.joblib"
