"""Re-run a library pair through the fixed pipeline and print region summary."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image as PILImage  # noqa: E402

from app.dda.geotiff_io import load_rgb_pil  # noqa: E402
from app.detection_config import get_load_max_side  # noqa: E402
import app.detection_engine as de  # noqa: E402

root = ROOT / "data/library_sources/central_delhi/Images"
name_b = sys.argv[1] if len(sys.argv) > 1 else "1.tif"
name_a = sys.argv[2] if len(sys.argv) > 2 else "2.tif"
method = sys.argv[3] if len(sys.argv) > 3 else "Hybrid AI"

b = load_rgb_pil(root / name_b, max_side=get_load_max_side())
a = load_rgb_pil(root / name_a, max_side=get_load_max_side())
if b.size != a.size:
    a = a.resize(b.size, PILImage.Resampling.LANCZOS)

mask, result, stats, regions = de.run_detection(
    b, a, method=method,
    enable_registration=True, enable_normalization=True,
    detection_sensitivity=0.45, min_region_area=150,
    before_path=str(root / name_b), after_path=str(root / name_a),
)

gsd = de._CURRENT_GSD_MPP
print("pair:", name_b, "vs", name_a, "| method:", method)
print("effective gsd:", round(gsd, 4) if gsd else None, "m/px")
print("change %:", round(stats["change_percentage"], 2))
print("regions:", len(regions))
for r in regions[:15]:
    m2 = r["area"] * gsd * gsd if gsd else 0.0
    print("  {:35s} conf={:.2f} area={:7d}px ({:7.0f} m2)".format(
        r["object_type"], r["confidence"], r["area"], m2))
