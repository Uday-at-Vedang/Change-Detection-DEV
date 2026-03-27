# Landslide Detection Integration Plan (Uttarakhand)

This note covers:
- candidate datasets for Uttarakhand landslide monitoring,
- model/research direction,
- system architecture for integration,
- preprocessing and feature extraction starter workflow.

## 1) Candidate Datasets (Uttarakhand + nearby Himalayan context)

Use a layered strategy (event inventory + optical + terrain + rainfall):

1. **Landslide Inventory / Event Data**
   - Geological Survey of India (GSI) landslide inventory products.
   - NRSC/Bhuvan and disaster mapping layers (where available for state districts).
   - State disaster management/public reports for dated event polygons/points.

2. **Optical Satellite Time Series**
   - **Sentinel-2 (10m/20m)** for frequent revisit and vegetation/soil change.
   - **Landsat-8/9 (30m)** for long historical baseline.
   - Optional high-resolution commercial tiles for selected validation zones.

3. **Terrain Data (critical for landslide susceptibility)**
   - **SRTM/ALOS/CartoDEM** DEM.
   - Derived slope, aspect, curvature, roughness, topographic wetness proxies.

4. **Rainfall / Trigger Data**
   - IMD gridded rainfall, GPM/IMERG rainfall products.
   - Cumulative rainfall windows (1-day, 3-day, 7-day, 15-day anomalies).

5. **Ancillary Layers**
   - Landcover/forest loss,
   - road and river proximity,
   - settlements/infrastructure overlays for risk prioritization.

## 2) Model/Research Direction

Recommended progression:

### Phase A (already started in-app)
- Rule-based bi-temporal landslide candidate detection:
  - vegetation loss proxy,
  - bare-soil increase,
  - texture and edge disruption,
  - connected-component region extraction.

### Phase B (ML baseline)
- Pixel/patch classifier (Random Forest / XGBoost) using:
  - optical change features,
  - terrain derivatives,
  - rainfall context,
  - neighborhood statistics.

### Phase C (Deep Learning)
- U-Net/DeepLab/SegFormer style landslide segmentation with multi-channel input:
  - pre-event image,
  - post-event image,
  - DEM-derived bands (slope/aspect),
  - rainfall summary channels.

### Research papers to review first
- Remote sensing landslide mapping with deep learning in Himalayan terrain.
- Bi-temporal change detection for landslide scars (optical and SAR fusion).
- DEM + rainfall + optical hybrid susceptibility modeling.

## 3) Architecture for Integration (Current App)

Integrated design implemented in the app:

- New detection menu in UI:
  - `General Change Detection` (existing pipeline),
  - `Landslide Detection (Uttarakhand)` (separate pipeline).

- Shared API entrypoint:
  - `POST /api/detect`
  - new form field `detection_type`.

- Routing:
  - `detection_type=change_detection` -> `app/detection_engine.py`
  - `detection_type=landslide_detection` -> `app/landslide_engine.py`

- Shared output contract:
  - overlay image,
  - stats,
  - regions list,
  - history storage compatible with existing UI and DB.

This keeps current production behavior intact while enabling model-specific evolution for landslide.

## 4) Preprocessing and Feature Extraction (Starter)

Current landslide starter logic (`app/landslide_engine.py`) includes:

1. **Preprocessing**
   - RGB conversion, controlled resizing.

2. **Feature channels**
   - Green-index drop (vegetation loss proxy),
   - Soil score increase (HSV warm/dry proxy),
   - Texture roughness change (Laplacian-based),
   - Edge disruption map (Canny difference).

3. **Fusion + threshold**
   - weighted fusion of channels,
   - sensitivity-driven percentile threshold.

4. **Post-processing**
   - morphology cleanup,
   - region extraction with confidence/severity assignment.

## 5) Immediate next execution tasks

1. Build a curated Uttarakhand event list (district/date) and collect before/after pairs.
2. Generate DEM derivatives for those AOIs (slope/aspect/curvature).
3. Create a labeling protocol (landslide polygon + confidence tier).
4. Add benchmark script (precision/recall/F1/IoU per district/event).
5. Move from Rule-Based v1 to ML baseline (RF/XGBoost) with reproducible feature table.

