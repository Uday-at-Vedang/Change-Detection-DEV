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
| `APP_MODE` | Set to `dda` on **satdetect-dev** only (enables DDA library UI) |
| `LOCAL_LIBRARY_ROOT` | Path to year folders (default: `library_sources/` in project) |
| `MAX_GEOTIFF_MB` | Library GeoTIFF upload cap (default **5120** = 5 GB on dev) |
| `MAX_IMAGE_MB` | PNG/JPEG library cap (default 50 MB) |
| `SECRET_KEY` | Optional legacy JWT setting (login disabled) |
| `DATABASE_URL` | PostgreSQL instead of SQLite (optional) |
| `SMTP_USER` / `SMTP_PASS` | Email notifications via Gmail SMTP |
| `EMAIL_API_URL` | Custom email API (default in code) |

Dev Space can omit `SECRET_KEY` (login is disabled on both Spaces).

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
