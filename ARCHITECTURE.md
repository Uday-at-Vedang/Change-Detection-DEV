# AI Change Detection — Architecture & Component Guide

## Overview

AI Change Detection is a full-stack web application that detects, classifies, and visualizes changes between two satellite or aerial images (before and after). It uses multi-signal image processing — combining color analysis, structural similarity, texture descriptors, and edge detection — to identify ground-level changes such as new buildings, deforestation, road construction, and more.

---

## System Architecture

```
┌─────────────────────────────────────────────────┐
│                   Browser (SPA)                  │
│  index.html  +  style.css  +  app.js            │
│  Login / Register / Upload / Results / History   │
└────────────────────┬────────────────────────────┘
                     │  REST API (JSON + multipart)
┌────────────────────▼────────────────────────────┐
│              FastAPI Backend (main.py)            │
│  Auth routes  ·  Detection route  ·  History     │
├──────────┬──────────┬───────────────────────────┤
│ auth.py  │ models.py│  detection_engine.py       │
│ JWT auth │ ORM      │  Image processing pipeline │
├──────────┴──────────┤                            │
│   database.py       │  SQLite (local/HF Spaces)  │
└─────────────────────┴───────────────────────────┘
```

---

## File-by-File Component Breakdown

### 1. `app/main.py` — API Server & Route Definitions

**Role:** The central FastAPI application. Defines all HTTP endpoints, wires together authentication, database, and the detection engine.

**Use cases solved:**
- **User registration & login** (`POST /api/auth/register`, `POST /api/auth/login`) — Creates accounts and issues JWT tokens stored as HTTP-only cookies.
- **Session management** (`POST /api/auth/logout`, `GET /api/me`) — Clears auth cookies; returns the current authenticated user.
- **Password reset** (`POST /api/auth/reset-password`) — Allows users to reset their password by email (no email verification — documented limitation).
- **Change detection** (`POST /api/detect`) — Accepts two images (before/after) plus configuration options, runs the full detection pipeline, saves the overlay image to disk, stores run metadata in the database, and returns results with a base64-encoded overlay for immediate display.
- **History** (`GET /api/history`, `DELETE /api/history/{id}`) — Lists past detection runs for the logged-in user; allows deletion of individual runs and their overlay files.
- **Static file serving** — Mounts the `static/` directory for CSS/JS and serves `templates/index.html` as the SPA entry point.
- **Upload size limiting** — Rejects images larger than 20 MB to prevent out-of-memory crashes.

---

### 2. `app/auth.py` — Authentication & Authorization

**Role:** Handles password hashing, JWT token creation/validation, and user lookup.

**Use cases solved:**
- **Secure password storage** — Uses bcrypt via `passlib` to hash passwords before storing them. Plaintext passwords never touch the database.
- **Stateless authentication** — Issues HS256 JWT tokens with a 7-day expiry. Tokens are sent as HTTP-only cookies (for browser requests) and also accepted as Bearer tokens in the Authorization header.
- **Multi-source token resolution** — `get_current_user()` checks three sources in order: Authorization header, HTTP cookie, then form field. This ensures authentication works for both API clients and browser multipart form submissions.

---

### 3. `app/models.py` — Database Models (ORM)

**Role:** Defines the SQLAlchemy ORM models that map to database tables.

**Tables:**
- **`users`** — Stores account data: email, bcrypt-hashed password, full name, and creation timestamp. Each user has a one-to-many relationship with detection runs.
- **`detection_runs`** — Stores metadata for each detection run: title, method used, pixel statistics (total, changed, percentage), region count, path to the saved overlay image, and a JSON blob of all classified regions with their sub-types and 3D analysis data.

**Use case solved:** Persistent storage of user accounts and detection history so users can revisit past results.

---

### 4. `app/database.py` — Database Connection & Session Management

**Role:** Configures the SQLAlchemy engine, session factory, and data directory paths.

**Use cases solved:**
- **Environment-aware storage** — Detects Hugging Face Spaces (via `SPACE_ID` env var) and uses a writable home directory; falls back to a local `data/` directory for development.
- **Database portability** — Defaults to SQLite for zero-config local development. Supports PostgreSQL via `DATABASE_URL` environment variable for production (with automatic `postgres://` → `postgresql://` rewrite for SQLAlchemy 2.x compatibility).
- **Session lifecycle** — The `get_db()` generator provides request-scoped database sessions with automatic cleanup.

---

### 5. `app/detection_engine.py` — Image Processing & Classification Pipeline

This is the core of the application. It contains all image analysis algorithms organized into a sequential pipeline.

#### 5a. Pre-processing (`preprocess_image`)

**Use case:** Normalizes input images to a consistent format before analysis.
- Converts grayscale or RGBA images to RGB.
- Downscales images larger than 2000px on any side to prevent memory issues while preserving enough detail for analysis.

#### 5b. Image Registration (`register_images`)

**Use case:** Aligns the "after" image to the "before" image so pixel-level comparison is meaningful.
- Uses ORB feature detection to find matching keypoints between both images.
- Applies Lowe's ratio test to filter spurious matches.
- Computes a RANSAC homography to warp the after image into alignment.
- Rejects bad alignments via inlier ratio threshold and homography determinant check (catches degenerate transforms).
- **Why it matters:** Without alignment, even a slight camera angle difference would cause the entire image to appear "changed."

#### 5c. Radiometric Normalization (`normalize_radiometry`)

**Use case:** Corrects brightness and contrast differences between the two images caused by different lighting conditions, seasons, or sensors.
- Performs histogram matching in LAB color space (perceptually uniform) so the "after" image has the same color distribution as the "before."
- Applies CLAHE (Contrast Limited Adaptive Histogram Equalization) symmetrically to both images to enhance local contrast without introducing bias.
- **Why it matters:** A cloudy day vs. a sunny day would otherwise create massive false-positive "changes" everywhere.

#### 5d. SSIM Change Map (`compute_ssim_change_map`)

**Use case:** Measures structural dissimilarity between corresponding regions of both images.
- Computes per-pixel Structural Similarity Index (SSIM) using Gaussian-weighted windows.
- Outputs a dissimilarity map where 0 = identical and 1 = completely different.
- Clamps variance values to prevent numerical instability from floating-point rounding.
- **Why it matters:** SSIM captures structural patterns (edges, textures) that simple color difference would miss.

#### 5e. Texture Features (`compute_lbp`, `compute_texture_change`)

**Use case:** Detects changes in surface texture (e.g., smooth road vs. rough construction rubble).
- Computes Local Binary Patterns (LBP) — a texture descriptor that encodes the relationship between each pixel and its neighbors.
- The absolute difference between before/after LBP maps highlights regions where surface texture changed.
- **Why it matters:** Two regions can have similar colors but very different textures (e.g., grass vs. artificial turf, bare soil vs. concrete).

#### 5f. Edge Change Detection (`compute_edge_change`)

**Use case:** Identifies new or removed structural boundaries.
- Uses Canny edge detection with adaptive thresholds based on median intensity.
- Dilates edges so nearby edges match (tolerates minor misalignment).
- Computes the absolute difference to find edges present in one image but not the other.
- **Why it matters:** New buildings, roads, and demolished structures introduce or remove strong edges.

#### 5g. Detection Methods (4 options)

| Method | Description | Best For |
|--------|-------------|----------|
| **Image Difference** | LAB color space Delta-E difference with Otsu thresholding | Fast, simple comparisons with clear color changes |
| **Feature-Based** | Multi-space (LAB + HSV) feature clustering via KMeans | Subtle, distributed changes across large areas |
| **AI-Based Deep Learning** | Multi-signal fusion (color + SSIM + texture + edge) with entropy-weighted adaptive combination | Highest accuracy; catches both color and structural changes |
| **Hybrid Approach** | Weighted blend of all three methods (20% difference + 30% feature + 50% AI) | Maximum coverage when unsure which method is best |

**Image Difference** (`image_difference_method`):
- Converts to LAB and computes a weighted Delta-E (CIE76) difference per pixel.
- Applies Otsu's automatic threshold to separate "changed" from "unchanged."
- Fast but only catches color changes — misses structural changes where color stays similar.

**Feature-Based** (`feature_based_method`):
- Extracts LAB and HSV difference features per pixel.
- Downsamples to 250K pixels for KMeans clustering (4 clusters), then maps labels back to full resolution.
- The cluster with the highest mean feature magnitude is labeled "change."
- Good at finding spatially distributed or gradual changes.

**AI-Based Deep Learning** (`ai_deep_learning_method`):
- Fuses four independent signals: multi-scale LAB color difference, SSIM structural dissimilarity, LBP texture change, and Canny edge change.
- Weights each channel by its information entropy (channels with more discriminative power get higher weight).
- Applies Otsu thresholding followed by bilateral filtering for edge-preserving smoothing.
- The most accurate method — captures color, structure, texture, and edge changes simultaneously.

**Hybrid** (`hybrid_method`):
- Runs all three methods and combines their masks with fixed weights.
- AI method gets the most weight (50%) as it's most reliable.

#### 5h. Post-processing (`_clean_mask`)

**Use case:** Cleans up raw change masks to remove noise and fill gaps.
- Morphological closing (fills small gaps inside detected regions).
- Morphological opening (removes small noise speckles).
- Contour-based hole filling (ensures detected regions are solid).
- Sensitivity parameter controls the aggressiveness of gap-filling.

#### 5i. Visualization (`visualize_changes`)

**Use case:** Creates the final overlay image showing detected changes.
- Draws a semi-transparent red overlay on all changed pixels in the "after" image.
- Draws white bounding boxes around each classified region.
- Annotates regions with labels showing sub-type, building stories, and construction stage.

#### 5j. Object Classification (`classify_object_type`, `classify_with_ensemble`)

**Use case:** Determines what kind of ground-level change each detected region represents.

**Feature extraction** (`extract_advanced_features`):
- Extracts 17 features per region: color stats (RGB, HSV, LAB means/stds), vegetation index (NDVI), texture (LBP variance, standard deviation), edges (density, orientation entropy), GLCM-like contrast, shape ratios, and color ratios.

**Transient object filtering** (`_is_transient_object`):
- Filters out non-permanent changes: people, vehicles, animals, shadows.
- Uses area, aspect ratio, edge density, and texture variance thresholds.

**Classification categories:**
| Category | Key Indicators |
|----------|---------------|
| New Construction/Building | Low orientation entropy, compact shape, moderate edges, low saturation |
| Demolition/Clearing | High texture variance, high entropy, bright, low vegetation |
| Vegetation Change | High NDVI, green ratio, medium texture, high saturation |
| Water Body Change | High blue ratio, low texture, smooth surface, specific hue range |
| Road/Pavement Change | Elongated shape, homogeneous color, low saturation, low entropy |
| Bare Land/Soil Change | High red ratio, low NDVI, low texture, earth-tone hue |

**Ensemble voting** (`classify_with_ensemble`):
- Classifies the full region plus 5 sub-regions (quadrants + center).
- Combines votes weighted by confidence.
- Boosts confidence when ≥60% of sub-regions agree.
- Only excludes a region as transient if a majority of sub-regions are flagged.

#### 5k. Vegetation Sub-Classification (`classify_vegetation_subtype`)

**Use case:** Provides detailed breakdown of vegetation changes by comparing the before and after region crops.

| Sub-Type | Detection Logic |
|----------|----------------|
| Deforestation/Tree Removal | Before was green (high NDVI), after is bare (NDVI dropped, brightness increased) |
| New Vegetation/Growth | Before was bare, after is green (NDVI increased, saturation up) |
| Crop/Agricultural Change | Regular texture patterns in either image, moderate NDVI shift, large area |
| Vegetation Health Decline | Both images green but NDVI and saturation slightly decreased (browning/drought) |
| Seasonal Variation | Mild color/texture shift, both images still green |

#### 5l. Structural Sub-Classification (`classify_structural_subtype`)

**Use case:** Provides detailed breakdown of structural and road changes by comparing before/after structure presence, edge density, and texture.

| Sub-Type | Detection Logic |
|----------|----------------|
| New Building | No structure before, structure detected after |
| Building Expansion | Structure in both images, but after has higher edge density |
| Renovation/Modification | Structure in both, similar density but different appearance (brightness/saturation shift) |
| Partial Demolition | Structure before, reduced edges after |
| Full Demolition | Structure before, bare/empty after |
| Infrastructure Change | Elongated shape with high geometric regularity |
| New Road/Pavement | No structure before, structure after (for road-type regions) |
| Road Widening | Structure in both, edge density increased |
| Road Resurfacing | Structure in both, similar edges but brightness changed |
| Road Deterioration | Texture increased, edges decreased (surface degradation) |

#### 5m. 3D Building Analysis

**Use case:** Estimates building height/stories and classifies construction stage for building-type regions.

**Shadow-based height estimation** (`estimate_building_height`):
- Detects new shadow pixels adjacent to the building by comparing brightness in the expanded bounding box area between before and after images.
- Measures shadow length as the maximum spatial extent of the shadow cluster.
- Combines shadow ratio, footprint geometry (compact = likely taller), and texture regularity to estimate story count.
- Assumes 3 meters per story.

**Construction stage classification** (`classify_construction_stage`):
| Stage | Visual Indicators |
|-------|------------------|
| Foundation | Low texture, low edges, homogeneous, soil/concrete colors |
| Structural | High edges, geometric regularity (low entropy), high GLCM contrast |
| Under Construction | Mixed materials, irregular texture, medium edge density |
| Complete | Uniform roof, clean edges, low entropy, consistent color |

---

### 6. `templates/index.html` — Frontend Structure

**Role:** Single-page application (SPA) with four views managed by JavaScript.

**Views:**
- **Login** — Email/password form with password visibility toggle and "Forgot password" link.
- **Register** — Account creation form with name, email, and password.
- **Forgot Password** — Email + new password form (simplified reset without email verification).
- **Dashboard** — The main workspace containing:
  - **Upload zone** — Drag-and-drop or click-to-browse for before/after images with live previews.
  - **Detection options** — Method selector, title input, image registration and radiometric normalization toggles.
  - **Results panel** — Statistics boxes (change %, changed pixels, total pixels, region count), before/after comparison slider, and a detailed regions table with columns for change type, sub-type, confidence, area, stories, height, construction stage, and center coordinates.
  - **History** — List of past detection runs with view and delete actions.

---

### 7. `static/css/style.css` — Visual Design

**Role:** Complete styling for the application using CSS custom properties and a gradient theme.

**Key design decisions:**
- **Color theme** — Linear gradient from `#2e33c5` (blue) to `#cf2040` (red) applied to the navbar, footer, buttons, and accent elements. White background for content areas.
- **Sticky navbar** — Full-viewport-width gradient bar that stays fixed at the top during scrolling.
- **Mobile responsive** — Grid layouts collapse to single columns on screens under 768px. Font sizes, padding, and table layouts adapt to small screens.
- **Glassmorphism dropdown** — User account menu uses a gradient background with subtle shadow for the avatar popup.
- **Compare slider** — Custom CSS for the before/after image comparison handle with gradient accents.
- **Stat boxes** — Responsive font sizing with `clamp()` and compact number formatting to prevent overflow.

---

### 8. `static/js/app.js` — Client-Side Logic

**Role:** All browser-side interactivity — authentication flow, file uploads, API communication, result rendering, and UI state management.

**Key responsibilities:**
- **Authentication** — Login, register, forgot-password form handlers that call the API and store JWT tokens in localStorage. Automatic session check on page load (`init()`).
- **File upload** — Drag-and-drop and click-to-browse handlers with live image previews.
- **Detection submission** — Builds a FormData payload with images and settings, sends to `/api/detect`, and renders results.
- **Result rendering** — Populates stat boxes with compact-formatted numbers, fills the regions table, sets up the before/after comparison slider, and scrolls to results.
- **History management** — Loads past runs, renders them with view/delete actions, and handles deletion with an animated confirmation modal.
- **UI utilities** — View switching, error/success alerts, avatar dropdown toggle, password visibility toggle, and HTML escaping.

---

### 9. `Dockerfile` — Container Configuration

**Role:** Defines the Docker image for deployment on Hugging Face Spaces.

**Key aspects:**
- Base image: `python:3.11-slim` with OpenCV system dependencies (`libgl1`, `libglib2.0-0`, etc.).
- Non-root user (`appuser`) as required by Hugging Face Spaces security policy.
- Runs Gunicorn with a single Uvicorn worker on port 7860 (HF Spaces default) with 120-second timeout for long detection runs.
- Single worker prevents SQLite write-lock race conditions.

---

### 10. `requirements.txt` — Python Dependencies

| Package | Purpose |
|---------|---------|
| `fastapi` | Web framework for the REST API |
| `uvicorn` | ASGI server (production via Gunicorn) |
| `gunicorn` | Process manager for production deployment |
| `python-multipart` | Handles multipart/form-data file uploads |
| `sqlalchemy` | ORM for database operations |
| `psycopg2-binary` | PostgreSQL driver (for production databases) |
| `python-jose` | JWT token encoding/decoding |
| `passlib` + `bcrypt` | Password hashing (bcrypt pinned to 4.0.1 for compatibility) |
| `pillow` | Image loading and format conversion |
| `numpy` | Numerical array operations for image processing |
| `opencv-python-headless` | Core image processing (registration, morphology, edge detection, color conversion) |
| `scikit-learn` | KMeans clustering for feature-based detection; StandardScaler for normalization |
| `scipy` | Scientific computing utilities |

---

## Data Flow: End-to-End Detection Run

```
1. User uploads before + after images via browser
       │
2. POST /api/detect (multipart form with images + settings)
       │
3. Authentication check (JWT from cookie/header/form)
       │
4. Pre-processing
   ├── Convert to RGB, limit to 2000px
   │
5. Image Registration (optional)
   ├── ORB keypoints → ratio test → RANSAC homography → warp
   │
6. Radiometric Normalization (optional)
   ├── LAB histogram matching + symmetric CLAHE
   │
7. Change Detection (selected method)
   ├── Image Difference: LAB Delta-E + Otsu
   ├── Feature-Based: LAB/HSV features + KMeans clustering
   ├── AI-Based: Multi-signal fusion (color + SSIM + texture + edge)
   └── Hybrid: Weighted combination of all three
       │
8. Post-processing
   ├── Morphological closing/opening + hole filling
       │
9. Region Analysis
   ├── Connected components → bounding boxes
   ├── Feature extraction (17 features per region)
   ├── Transient object filtering (remove people, cars, etc.)
   ├── Primary classification (6 ground-level categories)
   ├── Ensemble voting (full region + 5 sub-regions)
   │
10. Sub-Classification
    ├── Vegetation: Deforestation / Growth / Crop / Health / Seasonal
    ├── Structural: New / Expansion / Renovation / Partial Demo / Full Demo / Infrastructure
    └── Road: New / Widening / Resurfacing / Deterioration
       │
11. 3D Building Analysis (for construction/demolition regions)
    ├── Shadow detection → height/stories estimation
    └── Construction stage classification
       │
12. Visualization
    ├── Red overlay on changed pixels
    ├── Bounding boxes + annotation labels
       │
13. Response
    ├── Save overlay to disk + database record
    └── Return JSON: stats, regions, base64 overlay image
```

---

## Deployment

- **Local development:** Run `uvicorn app.main:app --reload` from the project root. SQLite database is created in `data/satellite_app.db`.
- **Hugging Face Spaces:** Push to the linked HF repo. The Dockerfile builds and deploys automatically. Database is stored at `/home/appuser/data/` (ephemeral — resets on container restart).
- **Production with PostgreSQL:** Set `DATABASE_URL` environment variable to a PostgreSQL connection string. Set `SECRET_KEY` to a strong random value.
