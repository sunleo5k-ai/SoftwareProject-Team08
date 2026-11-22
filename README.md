# SmartPark – AI-Powered Smart City Parking Network

SWE 401 Software Engineering · Course Project · Fall 2026 · Abu Dhabi University
Instructor: Dr. Muhammad Nasir Mumtaz Bhutta

## Team members

| Member | Student ID | Main modules |
|---|---|---|
| Abdelrahman Abdellatif | 1099415 | auth, reservation, payment, database design |
| Mohammad Khan | 1097441 | forecast (AI), pricing, web client |
| Mohammed Alnuaimi | 1095848 | gate-access, admin, reporting, testing |

## Problem

Drivers in Abu Dhabi and Dubai spend several minutes per trip circling for a parking spot
(40 % of our 43 survey respondents said about 7 minutes). Existing UAE systems (Mawaqif,
Parkin, Parkonic) handle payment and ticketless entry, but none of them let a driver
**reserve** a specific spot in advance, none of them use **demand-based pricing**, and none
of them show a **combined view** of free spots across garages.

SmartPark is a parking network that shows live availability per floor, predicts demand
with machine learning, prices each hour fairly from that prediction, and lets drivers
reserve, pay and enter by plate recognition or QR code.

## Key features

| Feature | Requirement |
|---|---|
| Register with OTP verification, role-based login, lock-out after 5 failed attempts | FR-01, FR-02 |
| Save vehicles (incl. EV and accessible-permit vehicles) | FR-03 |
| Search garages, sort by distance / price / free spots, free spots per floor and type | FR-04 – FR-06 |
| Reserve a spot for a period, 5-minute hold, no double booking, QR pass | FR-07 – FR-09 |
| Cancel or change a booking with automatic refund rules, no-show release | FR-10, FR-11, FR-16 |
| Full price breakdown; AI demand multiplier capped and floored; no surcharge on accessible spots | FR-12, FR-13, NFR-20 |
| Payment through a gateway token (no card data stored), receipts and history | FR-14, FR-15, NFR-10 |
| 24-hour occupancy forecast, busy/quiet indicator, cheapest times, retraining | FR-17 – FR-19 |
| ANPR entry with QR fallback, exit with overstay fee, attendant manual override | FR-20 – FR-23 |
| Floor map with route to the spot; notifications by push / SMS / email | FR-24, FR-25 |
| Admin: garages, floors, zones, spots; versioned pricing rules with preview; reports and CSV; users and audit log | FR-26 – FR-29 |
| Corporate accounts with one consolidated monthly invoice | FR-30 |

## Technologies

* **Backend:** Python 3.10+, FastAPI (REST API, OpenAPI docs), SQLAlchemy 2 ORM
* **Database:** SQLite by default; PostgreSQL through `DATABASE_URL`
* **AI / prediction:** scikit-learn `RandomForestRegressor`, NumPy, joblib
* **Security:** bcrypt password hashing, server-side sessions with 15-minute idle expiry, role-based access control
* **Web client:** HTML, CSS and plain JavaScript (single-page app, no build step), served by FastAPI
* **Other:** qrcode (QR passes), pytest + httpx (tests), Git / GitHub
* **Simulated external systems:** payment gateway (Stripe-style test tokens), ANPR cameras, occupancy sensors, notification service

## Prerequisites

* Python **3.10 or newer** (`python --version`)
* Git
* A web browser (Chrome, Edge, Firefox or Safari)

## Installation

```bash
git clone https://github.com/Abdelrahman4k/SoftwareProject-Team08.git smartpark
cd smartpark
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt
```

## Configuration

All settings have working defaults. To change them, copy `.env.example` to `.env`:

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | SQLite file in `data/` | Use PostgreSQL, e.g. `postgresql+psycopg2://user:pass@localhost/smartpark` (install `psycopg2-binary`) |
| `DEMO_MODE` | `true` | Shows the OTP on screen and enables the Demo Tools page |
| `DEVICE_KEY` | `demo-device-key` | Key used by lane cameras, QR readers and sensors |
| `SESSION_IDLE_MINUTES` | `15` | Session idle timeout |
| `JOB_INTERVAL_SECONDS` | `30` | How often scheduled jobs run |

On first start the database is created and filled with demo data (3 Abu Dhabi garages,
192 spots, 8 weeks of occupancy history, 14 days of bookings) and the forecast model is trained.
This takes about 10 seconds. Delete `data/smartpark.db` to start again from scratch.

## How to run

```bash
python -m uvicorn backend.app.main:app --reload
```

or double-click `run.bat` (Windows) / run `./run.sh` (macOS/Linux), which also creates the
virtual environment the first time.

Open **http://127.0.0.1:8000**. API documentation: **http://127.0.0.1:8000/docs**.

Demo accounts:

| Role | Email | Password |
|---|---|---|
| Driver | driver@smartpark.ae | Driver@1234 |
| Corporate client | corporate@smartpark.ae | Corp@1234 |
| Attendant | attendant@smartpark.ae | Attend@1234 |
| Administrator | admin@smartpark.ae | Admin@1234 |

Test payment methods: *Visa / Mastercard / Apple Pay* succeed, *Test card – always declined*
is declined, *Test – gateway not responding* simulates a timeout.

### Suggested live-demo script (about 4 minutes)

1. **Driver** – Find Parking → pick a garage → show the forecast → book a spot starting in 15 minutes → pay with the declined card, then Visa → show the QR pass and floor map.
2. **Attendant** – Lane Console → *Simulate* the expected arrival → ADMIT. Try confidence 0.70 → DENY (QR needed). Record a manual override.
3. **Admin** – Dashboard (occupancy, revenue, no-shows) → Pricing Rules (try cap 2.5 → error, preview, save) → Demo Tools: +30 min to show the reminder / no-show release.

## How to test

```bash
python -m pytest            # 43 automated tests, about 15 seconds
python -m pytest -v -s      # verbose, prints the measured response time and model error
python -m pytest --cov=backend/app   # code coverage (92 % at submission)
```

Each test is named after its ID in the traceability matrix (`TC-01` … `TC-30`, `CT-01`,
`UT-02`, `ML-01`, `PT-01`, `SEC-01` … `SEC-04`), so a requirement can be traced to the
test that verifies it.

## Project structure

```
backend/app/
  main.py              FastAPI app, scheduler, static files
  config.py, clock.py  settings; Gulf Standard Time clock (with demo offset)
  database.py          SQLAlchemy engine/session
  models.py            entity classes = database tables
  security.py          password hashing, sessions, role checks
  routers/             boundary (REST) layer: auth, driver, gate, admin
  services/            control layer: auth, availability, reservation, pricing,
                       forecast, payment_gateway, gate, notification, report, audit
  seed.py              demo data
backend/tests/         pytest suite (TC-xx test cases)
frontend/              web client (index.html, styles.css, app.js)
docs/uml/              PlantUML sources of the Phase 3 and 4 diagrams
```

## Limitations of the prototype

* ANPR cameras, occupancy sensors, the payment gateway and SMS/email delivery are simulated.
* The web client is in English only; Arabic/right-to-left (NFR-18) is planned.
* Bookings are made atomic with an in-process lock, which is correct for one server process;
  a multi-server deployment would use PostgreSQL row locks (`SELECT … FOR UPDATE`).
