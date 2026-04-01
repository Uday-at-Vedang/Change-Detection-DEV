# Pothole Detection Integration (Research + Architecture)

## 1) Computer vision approaches for road damage detection

Typical successful families:

- **Object detection (bounding boxes)**: YOLOv5/YOLOv8, Faster R-CNN  
  - Pros: simple outputs, fast, easy UI.  
  - Cons: boxes are coarse; struggles with thin cracks and complex shapes.

- **Instance segmentation**: Mask R-CNN, YOLACT  
  - Pros: tighter region boundary and size estimation.  
  - Cons: heavier models, more training complexity.

- **Semantic segmentation**: U-Net / DeepLabv3+ / SegFormer  
  - Pros: best for pixel-level damage maps, severity estimation.  
  - Cons: needs mask labels; inference cost.

- **Two-stage pipelines**:
  1) road surface ROI extraction (segment road), then  
  2) damage detection inside road only  
  - Pros: reduces false positives (buildings, shadows, non-road textures).

## 2) Datasets and pretrained models (starting points)

Common public datasets (road damage + potholes):

- **RDD (Road Damage Dataset / Road Damage Detection)**  
  Includes potholes and other damage classes from multiple countries.

- **Pothole-600 / Pothole datasets on Kaggle**  
  Smaller but useful for prototyping.

- **CrackForest / CFD / other crack datasets**  
  More focused on cracks; can help pretraining for surface defects.

Practical approach:
- Use a model pretrained on COCO, then fine-tune on RDD/pothole datasets.
- For best results, fine-tune on **your region-specific imagery** (road texture and lighting differs).

## 3) Feasibility: drone vs satellite vs vehicle camera

- **Vehicle camera (recommended)**:
  - Highest feasibility for potholes.
  - Typical resolution and perspective supports pothole features.

- **Drone (good)**:
  - Works well at low altitude with good GSD (cm/px).
  - Requires flight plan and stable capture.

- **Satellite (usually not feasible)**:
  - Most satellite imagery is too low resolution for potholes.
  - Only very high-res (sub-10cm) commercial imagery could work, and still hard due to shadows and angle.

## 4) Detection pipeline (integrated with current system)

Implemented integration strategy:

- Add **Pothole Detection** as another detection type in the menu.
- Route through the existing `POST /api/detect` using:
  - `detection_type=pothole_detection`
  - `pothole_model=<selected>`
- Separate engine module:
  - `app/pothole_engine.py`

### Starter logic implemented (Rule-Based v1)

For fast CPU MVP (vehicle/drone imagery):
- shadow/dark region score (local brightness drop)
- rough texture score (Laplacian roughness)
- edge score (Canny)
- fuse + sensitivity percentile threshold
- region extraction + severity/confidence

## 5) Model architectures to implement next

- **YOLOv8n (boxes)** for fast detection and scalable deployment.
- **SegFormer-b0 / U-Net** for pixel-level damage mapping.
- Optional road ROI segmentation first to reduce false positives.

## 6) Immediate next steps for dataset preprocessing / feature extraction

1. Define input standard: camera height, FOV, resolution, and capture protocol.
2. Build a labeled dataset:
   - bounding boxes or masks for potholes,
   - metadata: day/night, wet/dry, shadows.
3. Add preprocessing:
   - road ROI extraction,
   - illumination normalization,
   - motion blur handling.
4. Train baseline YOLO model and integrate as `pothole_model=YOLOv8`.

