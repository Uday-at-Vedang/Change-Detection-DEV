# AI Change Detection — Project Handover Document

**Prepared by:** Development Team
**Date:** February 7, 2026
**Version:** 2.0
**Live URL:** [Hugging Face Spaces — AI Change Detection](https://huggingface.co/spaces/coderuday21/satdetect)

---

## 1. Executive Summary

AI Change Detection is a web-based application that automatically detects, classifies, and visualizes changes between two satellite or aerial images taken at different times. Users upload a "before" and "after" image, and the system identifies what changed on the ground — new buildings, deforestation, road construction, demolitions, water body changes, and more.

The application provides detailed reports including change statistics, object-level classification with sub-type breakdowns, 3D building analysis (estimated height and construction stage), and an interactive visual overlay highlighting all detected changes in red.

---

## 2. Problem Statement

Manually comparing satellite imagery to identify ground-level changes is:

- **Time-consuming** — Analysts spend hours visually scanning large image pairs pixel by pixel.
- **Error-prone** — Human eyes miss subtle changes in vegetation health, gradual construction, or surface texture shifts.
- **Inconsistent** — Different analysts may classify the same change differently.
- **Unscalable** — As the volume of imagery grows (urban planning, environmental monitoring, disaster response), manual review cannot keep pace.

**AI Change Detection solves this** by automating the entire detection-to-classification pipeline, delivering consistent, quantified results in seconds.

---

## 3. Target Users

| User Role | How They Use It |
|-----------|----------------|
| Urban planners | Track unauthorized construction, building expansion, and infrastructure changes |
| Environmental analysts | Monitor deforestation, vegetation health decline, and land-use changes |
| Disaster response teams | Assess structural damage after natural disasters by comparing pre/post imagery |
| Agricultural analysts | Detect crop changes, seasonal variation, and land conversion |
| Government agencies | Monitor compliance with zoning and environmental regulations |
| Construction firms | Track construction progress and stage classification over time |

---

## 4. Key Features

### 4.1 Multi-Method Change Detection

The system offers four detection methods, each suited to different scenarios:

| Method | Best For | Speed |
|--------|----------|-------|
| **Image Difference** | Quick scans where changes involve obvious color shifts (e.g., cleared forest) | Fastest |
| **Feature-Based** | Subtle, distributed changes across large areas (e.g., gradual land-use shift) | Fast |
| **AI-Based Deep Learning** | Highest accuracy — combines color, structure, texture, and edge analysis | Moderate |
| **Hybrid Approach** | Maximum coverage when unsure which type of changes to expect | Slowest |

Users select the method per run depending on their accuracy vs. speed needs.

### 4.2 Intelligent Pre-Processing

Before comparing images, the system can automatically:

- **Align images** (Image Registration) — Corrects for camera angle, zoom, and position differences between the two photos so the comparison is pixel-accurate.
- **Normalize lighting** (Radiometric Normalization) — Corrects for brightness/contrast differences caused by weather, time of day, or different cameras, preventing false positives from lighting alone.

Both options are user-toggleable per run.

### 4.3 Ground-Level Change Classification

Every detected change region is automatically classified into one of six categories:

| Category | What It Detects |
|----------|----------------|
| **New Construction / Building** | New structures appearing on previously empty land |
| **Demolition / Clearing** | Structures or vegetation that have been removed |
| **Vegetation Change** | Any change in plant cover — forests, crops, parks |
| **Water Body Change** | New, expanded, or reduced water features |
| **Road / Pavement Change** | New roads, widened paths, resurfaced areas |
| **Bare Land / Soil Change** | Exposed earth, grading, land preparation |

**Transient objects are automatically filtered out** — people, vehicles, animals, and shadows are excluded so only permanent ground-level changes are reported.

### 4.4 Detailed Sub-Classification

Each primary category is further broken down into specific sub-types by comparing the "before" and "after" regions:

**Vegetation Sub-Types:**

| Sub-Type | What It Means |
|----------|---------------|
| Deforestation / Tree Removal | Green area has been cleared — trees or vegetation removed |
| New Vegetation / Growth | Previously bare area now has plant cover |
| Crop / Agricultural Change | Farmland has changed crop type or land use pattern |
| Vegetation Health Decline | Plants are browning or showing signs of drought/disease |
| Seasonal Variation | Normal seasonal color shift, both periods still green |

**Structural Sub-Types:**

| Sub-Type | What It Means |
|----------|---------------|
| New Building | Structure appeared where none existed before |
| Building Expansion | Existing building has been extended or enlarged |
| Renovation / Modification | Building exists in both images but has changed appearance |
| Partial Demolition | Part of a structure has been removed |
| Full Demolition | Entire structure has been removed, area is now bare |
| Infrastructure Change | Non-building structures (bridges, towers, utilities) |

**Road Sub-Types:**

| Sub-Type | What It Means |
|----------|---------------|
| New Road / Pavement | Road appeared where none existed before |
| Road Widening | Existing road has been expanded |
| Road Resurfacing | Road surface has been repaved (different appearance, same width) |
| Road Deterioration | Road surface has degraded |

### 4.5 3D Building Analysis

For regions classified as buildings or construction, the system provides:

- **Estimated Stories** — Number of floors, calculated from shadow analysis and building footprint geometry.
- **Estimated Height** — Height in meters (based on 3m per story).
- **Construction Stage** — Classified as Foundation, Structural, Under Construction, or Complete based on visual features.

### 4.6 Interactive Visual Output

- **Red overlay** — All detected changes are highlighted in red on the "after" image.
- **Before/After slider** — A draggable comparison slider lets users visually inspect what changed by sliding between the original and the annotated result.
- **Annotated bounding boxes** — Each detected region gets a labeled bounding box showing its sub-type, stories, and construction stage.

### 4.7 User Accounts & History

- **Secure login system** — Email/password registration with encrypted password storage.
- **Detection history** — All past runs are saved with their results, overlay images, and metadata. Users can revisit or delete previous runs.
- **Password reset** — Users can reset their password from the login screen.

### 4.8 Responsive Design

The application is fully functional on desktop browsers and mobile devices, with an optimized layout for smaller screens.

---

## 5. Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Frontend** | HTML, CSS, JavaScript (Vanilla) | Single-page application — no framework dependencies |
| **Backend** | Python, FastAPI | REST API server handling authentication and detection |
| **Image Processing** | OpenCV, NumPy, Scikit-learn, Pillow | Core algorithms for detection, classification, and visualization |
| **Database** | SQLite (local), PostgreSQL (production-ready) | User accounts and detection run history |
| **Authentication** | JWT tokens, bcrypt password hashing | Secure stateless authentication |
| **Deployment** | Docker, Hugging Face Spaces | Containerized cloud deployment |

---

## 6. Application Screens

### Login / Registration
- Clean login form with email and password
- Password visibility toggle (eye icon)
- "Forgot password" option
- Toggle between sign-in and registration

### Dashboard (Main Screen)
- **Upload Section** — Two drag-and-drop zones for before and after images with live previews
- **Configuration** — Method selector, run title, and optional pre-processing toggles
- **Results Section** — Appears after running detection:
  - Statistics bar: change percentage, changed pixels, total pixels, region count
  - Before/after comparison slider
  - Detailed table of all detected regions with type, sub-type, confidence, area, building data, and coordinates
- **History Section** — List of all past runs with option to view overlay or delete

---

## 7. Output Data Per Detection Run

Each run produces the following data:

| Field | Description |
|-------|-------------|
| Change Percentage | Proportion of the image area that changed |
| Changed Pixels | Total number of pixels detected as changed |
| Total Pixels | Total image area in pixels |
| Regions Count | Number of distinct change regions identified |
| **Per Region:** | |
| Change Type | Primary category (Construction, Vegetation, etc.) |
| Sub-Type | Detailed sub-classification (New Building, Deforestation, etc.) |
| Confidence | Classification confidence score (0–100%) |
| Area | Region size in pixels |
| Estimated Stories | Number of building floors (building regions only) |
| Estimated Height | Height in meters (building regions only) |
| Construction Stage | Foundation / Structural / Under Construction / Complete (building regions only) |
| Center Coordinates | Location of the region center in the image |
| Overlay Image | Annotated image with red highlights and bounding boxes |

---

## 8. Deployment & Access

### Current Deployment
- **Platform:** Hugging Face Spaces (free tier)
- **URL:** https://huggingface.co/spaces/coderuday21/satdetect
- **Container:** Docker-based, auto-deploys on code push
- **Database:** SQLite (ephemeral — resets when the container restarts)

### Limitations of Current Deployment
| Limitation | Impact | Mitigation Path |
|-----------|--------|-----------------|
| Ephemeral database | User accounts and history are lost on container restart | Migrate to external PostgreSQL database |
| No email verification on password reset | Anyone who knows an email can reset the password | Implement token-based email verification flow |
| Single-worker server | Cannot handle high concurrent load | Scale to multiple workers with PostgreSQL |
| Free-tier resources | Limited CPU/memory, images capped at 2000px and 20MB | Upgrade to paid hosting for larger images |

### For Production Deployment
To move from the current demo to a production environment, the following would be needed:
1. External PostgreSQL database for persistent storage
2. Email service integration (SendGrid, AWS SES) for password reset verification
3. Paid cloud hosting (AWS, GCP, or Azure) for better performance
4. HTTPS with a custom domain
5. Environment-based secret key management

---

## 9. How Accuracy Works

The system does **not** use a pre-trained deep learning model (no neural network). Instead, it uses a **multi-signal fusion approach** — combining multiple independent analysis methods that each measure a different aspect of change:

1. **Color difference** — Measures how much the color changed at each pixel (in perceptually uniform color space).
2. **Structural similarity** — Measures whether the patterns and structures at each pixel changed.
3. **Texture analysis** — Measures whether the surface texture changed (smooth → rough, etc.).
4. **Edge detection** — Measures whether structural boundaries appeared or disappeared.

These four signals are weighted by their discriminative power and fused into a single change map. This approach is:

- **Transparent** — Every decision can be traced to specific visual features (no "black box").
- **Deterministic** — The same inputs always produce the same outputs.
- **No training data required** — Works on any satellite/aerial imagery without needing labeled datasets.

**Trade-off:** A trained deep learning model (e.g., U-Net, Siamese network) would likely achieve higher accuracy on specific datasets, but would require labeled training data and GPU resources. The current approach provides good accuracy out-of-the-box for general-purpose change detection.

---

## 10. Future Enhancement Opportunities

| Enhancement | Business Value |
|-------------|---------------|
| Deep learning model integration (U-Net / Siamese CNN) | Significantly higher detection accuracy on trained domains |
| Batch processing / multi-image upload | Analyze time-series imagery for trend analysis |
| GIS integration (GeoTIFF, coordinate mapping) | Pin changes to real-world geographic coordinates |
| PDF/Excel report export | Downloadable reports for stakeholder distribution |
| Webhook / API integration | Automate detection as part of larger monitoring pipelines |
| Role-based access (admin, analyst, viewer) | Team collaboration with permission controls |
| Notification system | Alert stakeholders when significant changes are detected |
| Cloud-optimized image storage | Handle very large satellite tiles (10,000+ pixels) |

---

## 11. Repository Structure

```
change_detection_webapp/
├── app/
│   ├── main.py              → API server and route definitions
│   ├── auth.py              → Authentication (JWT, password hashing)
│   ├── models.py            → Database table definitions
│   ├── database.py          → Database connection setup
│   └── detection_engine.py  → All image processing and classification algorithms
├── templates/
│   └── index.html           → Frontend page structure
├── static/
│   ├── css/style.css        → All visual styling
│   └── js/app.js            → Client-side logic
├── Dockerfile               → Container build instructions
├── requirements.txt         → Python package dependencies
└── ARCHITECTURE.md          → Technical architecture reference (for developers)
```

---

*For technical implementation details, refer to `ARCHITECTURE.md` in the project repository.*
