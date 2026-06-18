# DDA Change Detection — Local Dev Setup

This repo is the **development branch** of the satellite change-detection app (DDA SOW). It runs the full dev UI: image library, GeoTIFF comparison, async jobs, reports, and PDF export.

**Live dev Space (reference):** https://coderuday21-satdetect-dev.hf.space  
**Production Space (do not deploy this repo there without review):** https://coderuday21-satdetect.hf.space

---

## 1. Prerequisites

| Requirement | Notes |
|-------------|--------|
| **Python 3.10 – 3.12** | Tested with **3.11** (same as Docker). 3.13+ may have wheel issues for some packages. |
| **Git** | Clone this repository. |
| **~4 GB free disk** | PyTorch (CPU), transformers model cache, and sample GeoTIFFs. |
| **RAM 8 GB+ recommended** | Detection loads AdaptFormer; large GeoTIFFs use more RAM. |

### Windows (GeoTIFF / rasterio)

`rasterio` needs GDAL. Easiest options:

**Option A — pip wheels (try first):**
```powershell
pip install -r requirements.txt
python -c "import rasterio; print('rasterio OK', rasterio.__version__)"
```

**Option B — if rasterio fails, use Conda for GDAL then pip for the rest:**
```powershell
conda create -n dda-cd python=3.11 -y
conda activate dda-cd
conda install -c conda-forge gdal rasterio -y
pip install -r requirements.txt
```

**Option C — OSGeo4W:** Install [OSGeo4W](https://trac.osgeo.org/osgeo4w/) and ensure `gdal` is on `PATH` before `pip install rasterio`.

### macOS / Linux

```bash
# macOS (Homebrew)
brew install gdal

# Ubuntu/Debian
sudo apt-get install gdal-bin libgdal-dev
export GDAL_CONFIG=/usr/bin/gdal-config
pip install -r requirements.txt
```

---

## 2. Clone and install

```bash
git clone https://github.com/Uday-at-Vedang/Change-Detection-DEV.git
cd Change-Detection-DEV
python -m venv venv
```

**Windows:**
```powershell
venv\Scripts\activate
pip install -U pip setuptools wheel
pip install -r requirements.txt
```

**macOS / Linux:**
```bash
source venv/bin/activate
pip install -U pip setuptools wheel
pip install -r requirements.txt
```

> First `pip install` may take 10–20 minutes (PyTorch + transformers).

---

## 3. Environment variables (optional)

Copy the template and edit if needed:

```bash
cp .env.example .env
```

| Variable | Default (local) | Purpose |
|----------|----------------|---------|
| `APP_MODE` | `dda` (set by `run.py`) | `dda` = full dev UI; `legacy` = simple upload UI |
| `SECRET_KEY` | random fallback | Set in production |
| `DATABASE_URL` | SQLite in `data/` | PostgreSQL optional |
| `LOCAL_LIBRARY_ROOT` | `library_sources/` | Custom image library folder |
| `MAX_GEOTIFF_MB` | `5120` | Max GeoTIFF upload size (MB) |
| `DETECTION_MAX_SIDE` | `4096` local / `2048` HF | Max pixel side for detection |
| `EMAIL_API_URL` | manager API | Email notifications |
| `SMTP_USER` / `SMTP_PASS` | — | Use SMTP if API URL empty |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | Report links in emails |

Local dev does **not** require email config unless you test notifications.

---

## 4. Image library (local GeoTIFFs)

Place images under year folders (not committed to git — too large):

```
library_sources/
  2024/
    site_a.tif
  2025/
    site_b.tif
  2026/
```

See `library_sources/README.md` for details. Supported: `.tif`, `.tiff`, `.png`, `.jpg`.

After adding files, start the app and click **Image Library → Refresh**.

---

## 5. Run the app

```bash
python run.py
```

Opens **http://127.0.0.1:8000** with the DDA dev UI (3 tabs: Image Library, Change Detection, Reports).

Alternative (with auto-reload during development):

```bash
set APP_MODE=dda          # Windows
export APP_MODE=dda       # macOS/Linux
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### First run

- Creates `data/satellite_app.db` and `data/overlays/`.
- Downloads **AdaptFormer** model from Hugging Face on first detection (~500 MB). Requires internet.
- Seed data: Delhi zone/village hierarchy is loaded automatically in DDA mode.

### Health check

```bash
curl http://127.0.0.1:8000/health
```

Expected: `"appMode": "dda"`, `"status": "ok"`.

---

## 6. Using the dev UI

1. **Image Library** — scan year folders, upload GeoTIFFs (up to 5 GB), view hierarchy.
2. **Change Detection** — pick Base (T1) and Comparison (T2), run detection (async jobs on HF; sync locally).
3. **Reports** — history, PDF download, browser report at `/dda/reports/{id}`.
4. **Bell icon** — in-app notifications for completed jobs.
5. **Review (FR-08)** — Confirm / False Positive per region, export confirmed CSV, submit to dept API (`DEPT_API_URL`).

---

## 7. Project layout (DDA)

```
app/
  main.py              # FastAPI entry
  detection_engine.py  # Change detection pipeline
  dda/                 # DDA modules (library, jobs, reports, geo)
static/js/dda/         # Dev frontend
templates/index_dda.html
docs/IMPLEMENTATION_PLAN_DDA.md   # SOW phase plan
```

---

## 8. Troubleshooting

| Issue | Fix |
|-------|-----|
| `ImportError: rasterio` | Install GDAL (see §1), then reinstall rasterio |
| Simple upload UI instead of DDA tabs | Set `APP_MODE=dda` or use `python run.py` |
| Library empty | Add `.tif` files under `library_sources/YYYY/` and click Refresh |
| Detection slow / OOM | Lower `DETECTION_MAX_SIDE=2048` or use smaller images |
| Model download fails | Check internet; set `HF_HOME` to a writable folder |
| Port 8000 in use | Change `PORT` in `run.py` or use `--port 8001` with uvicorn |

---

## 9. Deploy to Hugging Face dev Space (maintainers)

See `DEPLOYMENT.md`. Dev Space remote:

```powershell
git remote add hf-dev https://huggingface.co/spaces/coderuday21/satdetect-dev
git push hf-dev master:main
```

Do **not** push `master` to production `satdetect` without explicit sign-off.

---

## 10. Key API endpoints (DDA)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health + app mode |
| GET | `/api/dda/local/images` | Library image list |
| POST | `/api/dda/jobs` | Queue async detection |
| GET | `/api/dda/reports/{id}/pdf` | PDF export |
| GET | `/dda/reports/{id}` | Browser report page |
| GET | `/api/history` | Detection run history |
