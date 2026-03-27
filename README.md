---
title: AI Change Detection
emoji: 🛰️
colorFrom: gray
colorTo: green
sdk: docker
app_port: 7860
---

# Satellite Change Detection — Standalone Web App

Standalone web application for satellite image change detection with **user accounts**, **database storage**, and a **clean, modern UI**.

## Features

- **Login / Register** — JWT-based auth, passwords hashed with bcrypt
- **Database** — SQLite (or set `DATABASE_URL` for PostgreSQL); stores users and detection runs
- **Change detection** — Same model as the original app: AI-based, image difference, feature-based, hybrid
- **Detection menu** — Choose between General Change Detection and Landslide Detection (Uttarakhand starter)
- **Object classification** — Changed regions labeled as Water, Vegetation/Tree, Building, Road, Bare Ground/Soil
- **History** — List of past runs with overlay images and stats
- **UI** — Single-page app with a dark, “control room” style and teal accents

## Setup

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
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```

4. Open **http://localhost:8000** in your browser.

## First run

- The SQLite DB and `data/` (overlay images) are created automatically on first use.
- Register a new account from the welcome screen, then sign in.
- Upload **Before** and **After** images, choose a method, and click **Run detection**.
- Results appear below; runs are saved in **History**.

## Configuration

- **Database**: set `DATABASE_URL` (e.g. `postgresql://user:pass@host/db`) to use another DB; otherwise SQLite under `data/satellite_app.db` is used.
- **JWT**: set `SECRET_KEY` in `app/auth.py` (or via env) in production.
- **Email**: By default, notifications are sent via the manager's email API (`https://emailservice.managemybusinessess.com/api/email/send`). Override with `EMAIL_API_URL` if needed. To use SMTP (e.g. Gmail) instead, set `EMAIL_API_URL` to empty and set `SMTP_USER` and `SMTP_PASS`.

- **Landslide module**:
  - Integrated at runtime through the same `/api/detect` endpoint using `detection_type=landslide_detection`.
  - Engine code: `app/landslide_engine.py`
  - Dataset preprocessing starter: `app/landslide_preprocessing.py`
  - Planning/research brief: `Landslide_Detection_Uttarakhand_Integration_Plan.md`

## Project layout

```
change_detection_webapp/
├── app/
│   ├── main.py           # FastAPI app, routes
│   ├── database.py       # SQLAlchemy, session
│   ├── models.py         # User, DetectionRun
│   ├── auth.py           # JWT, password hashing
│   └── detection_engine.py  # Change detection (no Streamlit)
├── static/
│   ├── css/style.css     # Styles
│   └── js/app.js         # Frontend logic
├── templates/
│   └── index.html        # Single-page UI
├── data/                 # Created at runtime (DB + overlays)
├── requirements.txt
└── README.md
```

## API (for integration)

- `POST /api/auth/register` — body: `{ "email", "password", "full_name" }`
- `POST /api/auth/login` — body: `{ "email", "password" }` → returns `access_token`
- `GET /api/me` — header: `Authorization: Bearer <token>`
- `POST /api/detect` — form: `before`, `after` (files), `method`, `title`, etc. → returns stats, regions, overlay base64
- `GET /api/history` — list of current user’s runs
- `GET /api/overlay/<path>` — serve saved overlay image
- `GET /health` — lightweight health check (no DB)

## Hugging Face: Space stuck on “Restarting”

1. Open your Space → **Settings** → under **Build**, click **Clear build cache** → Save. Then trigger a rebuild (push a commit or click **Restart**).
2. Check **Logs** (Build logs + App logs) for Python errors or “Killed” (out of memory).
3. In **Settings** → **Hardware**, try a slightly larger CPU/memory if available.
