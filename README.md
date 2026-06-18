---
title: DDA Change Detection (Dev)
emoji: 🛰️
colorFrom: gray
colorTo: green
sdk: docker
app_port: 7860
---

# DDA Change Detection — Development

Satellite / drone **change detection** web app for the DDA Scope of Work. This repository tracks **dev-only** features (`APP_MODE=dda`): image library, GeoTIFF comparison, async jobs, geo-referenced regions, reports, and PDF export.

> **New to the project?** Start with **[DEV_SETUP.md](DEV_SETUP.md)** — step-by-step install and run instructions for your machine.

| Environment | URL |
|-------------|-----|
| **Dev Space (HF)** | https://coderuday21-satdetect-dev.hf.space |
| **Local** | http://127.0.0.1:8000 after `python run.py` |

## Quick start

```bash
git clone https://github.com/Uday-at-Vedang/Change-Detection-DEV.git
cd Change-Detection-DEV
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS/Linux
pip install -r requirements.txt
python run.py
```

1. Add GeoTIFFs to `library_sources/2025/` (or other year folders).
2. Open **Image Library → Refresh**.
3. Use **Change Detection** to compare T1 vs T2.

Full details: **[DEV_SETUP.md](DEV_SETUP.md)** · SOW plan: **`docs/IMPLEMENTATION_PLAN_DDA.md`** · HF deploy: **`DEPLOYMENT.md`**

---

# Satellite Change Detection — Standalone Web App

Standalone web application for satellite image change detection with **database storage** and a **clean, modern UI**. Opens directly on the detection page — no sign-in required.

## Features

- **DDA dev UI** — Library, comparison, jobs, reports, PDF (when `APP_MODE=dda`)
- **Direct access** — upload and run detection immediately (no login)
- **Database** — SQLite (or set `DATABASE_URL` for PostgreSQL); stores detection runs
- **Change detection** — AdaptFormer deep learning + hybrid / difference methods
- **GeoTIFF library** — Year-folder library, 5 GB upload limit, lat/lng on regions
- **Object classification** — Changed regions labeled (Water, Vegetation, Building, Road, etc.)
- **History & reports** — Past runs, PDF export, email notifications

## Setup (legacy summary)

See **[DEV_SETUP.md](DEV_SETUP.md)** for the complete guide. Minimal steps:

1. **Create a virtual environment (recommended)**

   ```bash
   cd change_detection_webapp
   python -m venv venv
   source venv/bin/activate   # Windows: venv\Scripts\activate
   ```

2. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

3. **Run the app**

   ```bash
   python run.py
   # or: uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```

4. Open **http://localhost:8000** in your browser.

## First run

- The SQLite DB and `data/` (overlay images) are created automatically on first use.
- **DDA mode:** place images in `library_sources/YYYY/` and use the Library tab.
- **Legacy mode:** upload Before/After PNGs on the detection page.
- AdaptFormer model downloads from Hugging Face on first detection (~500 MB).

## Configuration

- **DDA mode:** `APP_MODE=dda` (default when using `run.py`). Copy `.env.example` → `.env` for optional overrides.
- **Database**: set `DATABASE_URL` (e.g. `postgresql://user:pass@host/db`) to use another DB; otherwise SQLite under `data/satellite_app.db` is used.
- **JWT**: set `SECRET_KEY` via env in production.
- **Email**: `EMAIL_API_URL` or `SMTP_USER` / `SMTP_PASS` — see `.env.example`.

## Project layout

```
change_detection_webapp/
├── app/
│   ├── main.py              # FastAPI app, routes
│   ├── dda/                 # DDA dev modules (library, jobs, reports)
│   └── detection_engine.py  # Change detection pipeline
├── static/js/dda/           # DDA frontend
├── templates/index_dda.html # DDA dev UI
├── library_sources/         # Local GeoTIFF library (year folders)
├── docs/IMPLEMENTATION_PLAN_DDA.md
├── DEV_SETUP.md             # Colleague setup guide
├── requirements.txt
└── run.py                   # Local launcher (APP_MODE=dda)
```

## API (DDA highlights)

- `GET /health` — app mode and version
- `GET /api/dda/local/images` — library image list
- `POST /api/dda/jobs` — queue async detection
- `GET /api/dda/reports/{id}/pdf` — PDF download
- `GET /dda/reports/{id}` — browser report page
- `POST /api/detect` — legacy multipart upload detect
- `GET /api/history` — detection run history

## Hugging Face: Space stuck on “Restarting”

1. Open your Space → **Settings** → under **Build**, click **Clear build cache** → Save. Then trigger a rebuild (push a commit or click **Restart**).
2. Check **Logs** (Build logs + App logs) for Python errors or “Killed” (out of memory).
3. In **Settings** → **Hardware**, try a slightly larger CPU/memory if available.
