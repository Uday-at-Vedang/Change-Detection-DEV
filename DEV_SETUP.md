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
6. **Session users** — Each browser gets isolated history via `dda_session_id` cookie (no login required).
7. **Admin** — Optional `DDA_ADMIN_EMAIL` / `DDA_ADMIN_PASSWORD` for admin role; `GET /api/dda/admin/status`.

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

---

## 11. Delhi evaluation workflow (accuracy calibration)

Full end-to-end reproduction of the Delhi accuracy-improvement sprint (see `Accuracy_Improvement_Plan.xlsx`). Every command below runs from the repo root.

### 11.1 Curate real Delhi pairs

`library_sources/` ships empty (real imagery isn't committed — see `library_sources/README.md`). Two ways to populate it:

```bash
# Free, openly-licensed Sentinel-2 imagery (10m GSD — good for large land-use
# change, vegetation, water bodies; not reliable for individual buildings/roads)
python scripts/build_delhi_pairs_sentinel2.py --count 32

# Or, once you have your own imagery (DDA-provided GeoTIFFs, drone, etc.) in
# library_sources/<year>/:
python scripts/build_delhi_manifest.py --scan      # see what's available
python scripts/build_delhi_manifest.py --add --before library_sources/2024/x.tif \
    --after library_sources/2026/x.tif --zone "..." --change-types building,road
python scripts/build_delhi_manifest.py --validate  # check coverage (>=30 pairs, all 4 change types)
```

### 11.2 Label ground-truth masks

```bash
# Semi-automated candidate generation (CVA + adaptive threshold + blob filter)
python scripts/generate_candidate_masks.py

# Review docs/delhi_eval/labels/candidates/previews/*.png (before | after | mask | overlay),
# then promote or reject each one:
python scripts/accept_candidate_mask.py --pair-id delhi_0001
python scripts/accept_candidate_mask.py --reject --pair-id delhi_0002
```
Candidate masks are a starting point, not ground truth — always eyeball the preview before promoting. See `docs/delhi_eval/manifest.json` notes per pair for known limitations found during review (e.g. diffuse changes needing hand-drawn rough polygons instead).

### 11.3 Run the detection harness against the labeled set

```bash
python scripts/compare_methods.py --manifest docs/delhi_eval/manifest.json \
    --methods "AI-Based Deep Learning,Feature-Based,Hybrid Approach" --sensitivities 0.5 \
    --out runs/delhi_baseline
```
Reports per-pair IoU/F1/precision/recall for every labeled pair (unlabeled pairs run without ground-truth scoring).

### 11.4 Grid-search calibration

```bash
python scripts/grid_search_calibration.py --methods "AI-Based Deep Learning" \
    --sensitivities 0.5 --cl-qs 0.80,0.85,0.90,0.92 --manifest docs/delhi_eval/manifest.json
```
Sweeps `sensitivity` / `DETECTION_FUSION` / `DETECTION_DL_FLOOR_BASE` / `DETECTION_CL_Q_BASE` and ranks configs by mean F1 in `runs/calibration/leaderboard.csv`. **Always verify a promising config against the synthetic regression suite (11.5) before promoting it** — the highest Delhi F1 isn't automatically safe; see `runs/calibration/best_params.json` for a real example of a config that won on Delhi but broke a regression gate.

### 11.5 Synthetic regression gate (mandatory before promoting any default)

```bash
python scripts/validate_detection.py --benchmark --method "AI-Based Deep Learning" \
    --sensitivity 0.5 --out runs/synthetic_check
```
Four cases: `inserted_buildings`, `brightness_only`, `misaligned_change`, `parked_cars` (the mandatory car/transient-FP guard — must stay F1=1.0). Pass `--method`/`--sensitivity` to test any calibrated config; set the relevant `DETECTION_*` env vars first to test non-default fusion parameters.

**Known gap:** the plan also references a LEVIR-CD regression case and a kappa statistic (Phase 0, marked "Complete"). Neither actually exists in this codebase as of the Day 5/6 calibration pass — only the 4 synthetic cases above are real, runnable gates. Treat any claim of "LEVIR-CD gate passing" as unverified until that harness is actually built.

### 11.6 Promote a verified config to production defaults

Once a config passes 11.5 unchanged, hardcode it as the new default in `app/detection_config.py` (e.g. `get_cl_q_base()`'s fallback value), then re-run 11.3 and 11.5 **with no env var set** to confirm the new default behaves identically to what was verified.
