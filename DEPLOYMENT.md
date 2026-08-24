# Deployment — Two Hugging Face Spaces

This project uses **two separate Hugging Face Spaces** so the live app stays stable while you build changes for a new client.

| Environment | Space | Git remote | Branch | URL |
|-------------|-------|------------|--------|-----|
| **Production** (current clients) | `coderuday21/satdetect` | `hf` | `production` | https://huggingface.co/spaces/coderuday21/satdetect |
| **Development** (new client work) | `coderuday21/satdetect-dev` | `hf-dev` | `master` | https://huggingface.co/spaces/coderuday21/satdetect-dev |

---

## One-time setup: create the dev Space

1. Open **https://huggingface.co/new-space**
2. Set:
   - **Owner:** `coderuday21`
   - **Space name:** `satdetect-dev`
   - **SDK:** Docker
   - **Visibility:** Public (or Private if you prefer)
3. Click **Create Space**.

Then add the dev remote (once):

```powershell
cd change_detection_webapp
git remote add hf-dev https://huggingface.co/spaces/coderuday21/satdetect-dev
```

If `hf-dev` already exists, update it:

```powershell
git remote set-url hf-dev https://huggingface.co/spaces/coderuday21/satdetect-dev
```

Push the current development code to the new Space:

```powershell
git push hf-dev master:main --force
```

> **First push only:** Hugging Face creates a starter README commit when you create the Space. Use `--force` once to replace it with your app. Later pushes can omit `--force`.

Or run the helper script:

```powershell
.\scripts\push_hf_dev.ps1
```

---

## Branch strategy

```
production  ──► hf (satdetect)         — live app (no login; direct to detection)
master      ──► hf-dev (satdetect-dev) — new client experiments and upcoming changes
```

- **`production`** — tracks the stable live release (currently same as `master`, no login).
- **`master`** — active development; push to the dev Space for testing before promoting to production.

Work on new client features on `master`. When ready for the live app, merge into `production` and push to `hf`.

---

## Deploy commands

### Production (live app — no login)

```powershell
git checkout production
git merge master
git push hf production:main
```

Or push `master` directly to production:

```powershell
git push hf master:main
```

Helper:

```powershell
.\scripts\push_hf_production.ps1
```

### Development (new client Space)

After every change you want on the dev Space:

```powershell
git checkout master
git push hf-dev master:main
```

Helper:

```powershell
.\scripts\push_hf_dev.ps1
```

---

## GitHub mirror

**Repo:** https://github.com/Uday-at-Vedang/DDA.ChangeDetection  
**Default branch on GitHub:** `main`

Push the full app from local `master`:

```powershell
git push origin master:main
```

Push the production branch too:

```powershell
git push origin production:main-production
```

Set upstream (once, after first push):

```powershell
git branch --set-upstream-to=origin/main master
```

---

## Environment variables (both Spaces)

Set these in each Space’s **Settings → Repository secrets / Variables** if needed:

| Variable | Purpose |
|----------|---------|
| `APP_MODE` | Set to `dda` on **satdetect-dev** only (enables Vedangsoft library UI) |
| `STORAGE_ROOT` | Tree library root directory (default: `data/library_sources/`) |
| `LOCAL_LIBRARY_ROOT` | Alias for storage root override |
| `MAX_GEOTIFF_MB` | Library GeoTIFF upload cap (default **5120** = 5 GB on dev) |
| `MAX_IMAGE_MB` | PNG/JPEG library cap (default 50 MB) |
| `SECRET_KEY` | Optional legacy JWT setting (login disabled) |
| `DATABASE_URL` | MySQL (production) instead of SQLite — `mysql+pymysql://user:pass@host:3306/db`; PostgreSQL also supported |
| `SMTP_USER` / `SMTP_PASS` | Email notifications via Gmail SMTP |
| `EMAIL_API_URL` | Custom email API (default in code) |
| `DEPT_API_URL` / `DEPT_API_KEY` | Departmental submit API (optional) |
| `DDA_ADMIN_EMAIL` / `DDA_ADMIN_PASSWORD` | Seed admin user on dev (role: admin) |
| `DDA_TRAINING_EXPORT_KEY` | Header `X-DDA-Training-Key` for false-positive export |

### Detection accuracy controls (optional, dev-first)

All default to the previous behavior, so leaving them unset changes nothing.

| Variable | Purpose |
|----------|---------|
| `DETECTION_MAX_SIDE` | Downscaled-path pixel cap (default 4096 local / 2048 hosted) |
| `DETECTION_INFERENCE_MODE` | `downscaled` (default) or `fullres_tiled` (native detail) |
| `DETECTION_FULLRES_MAX_SIDE` | Full-res cap in fullres mode; 0 = native (default 8192) |
| `DETECTION_WINDOWED_THRESHOLD` | Native side above which GeoTIFFs stream from disk windows (default 8192) |
| `DETECTION_TILE_MEMORY_MB` | RAM budget per array before switching to windowed streaming (default 1536) |
| `DETECTION_TILE_SIZE` / `DETECTION_TILE_OVERLAP` | Full-res tile geometry (default 512 / 0.25) |
| `DETECTION_MULTISCALE` | `off` or scale list e.g. `0.5,1.0,1.5` (max-fused for recall) |
| `DETECTION_FUSION` | `smart_union` (default) or `hysteresis` |
| `DETECTION_CLAHE` / `DETECTION_HIST_MATCH` | Preprocessing toggles (CLAHE on, hist-match off) |
| `DETECTION_SKIP_PREBLUR` | Skip denoise (auto-on in fullres mode) |
| `DETECTION_BORDER_MARGIN` | Mask border zeroing (auto: 4 fullres / 12 downscaled) |
| `DETECTION_TILE_BATCH` | Tiles per GPU forward pass (default 1) |
| `DETECTION_SAVE_PROB_MAP` | Save a per-run probability PNG for debugging (default off) |

Dev Space can omit `SECRET_KEY` (login is disabled on both Spaces).

---

## Tree library layout (unlimited depth)

Images are organized in a **configurable tree** (zone → area → year → image type, or any depth):

```text
library_sources/
  central_delhi/
    karol_bagh/
      2025/
        Images/
          satellite.tif
```

- **API:** `GET /api/dda/tree`, `POST /api/dda/tree/nodes`, upload via `POST /api/dda/tree/nodes/{id}/images/upload`
- **UI:** Recursive tree sidebar on Library and Change Detection tabs; **Manage** (admin) for create/rename/move/delete
- **Storage:** Slug-based disk paths; display names in `node_path`
- **Local folders:** Create folders under `library_sources/{zone}/{area}/…/` and drop files into `Images/`; click **Refresh** (or restart app) to sync disk → DB via `POST /api/dda/local/rescan`
- **Legacy:** Flat `library_sources/YEAR/` files auto-migrate to `Unassigned/Legacy/{year}/Images/` on startup

---

## UAT checklist (satdetect-dev)

Run before promoting any Vedangsoft feature to production:

1. **Health** — `GET /health` returns `status: ok`, `appMode: dda`, `dda.libraryImages` ≥ 0
2. **Tree library** — Create zone → area → year nodes (admin); upload GeoTIFF to node; tree + grid show breadcrumb
3. **Compare** — Tree sidebar filters image grid; T1/T2 dropdowns list all images; Run Detection completes with plausible lat/lng on georeferenced TIFFs
4. **Viewer** — Slider / T1 / T2 / Overlay modes; click region to locate
5. **Review** — Confirm and False Positive; Export confirmed CSV; Submit confirmed
6. **Reports** — PDF download; `/dda/reports/{id}` page; email link (if SMTP configured)
7. **Session isolation** — Two browsers see separate history (per-session cookie)
8. **Admin** — `GET /api/dda/admin/status` (analyst+); stale job reconcile after restart
9. **Training export** — Mark false positives → `GET /api/dda/training/export` (admin or export key)
10. **Security** — Path traversal blocked (`../` in library path); upload size limit enforced

Sign-off: DDA stakeholder approves sample runs on dev Space before any push to `satdetect` production.

---

## Quick reference

```powershell
# Daily work (new client)
git checkout master
# ... edit code ...
git add .
git commit -m "Your message"
git push hf-dev master:main

# Update live app (only when ready)
git checkout production
git merge master          # or cherry-pick specific commits
git push hf production:main
git checkout master
```
