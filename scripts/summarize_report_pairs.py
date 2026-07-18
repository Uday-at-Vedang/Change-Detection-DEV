"""Quick summary of the three PDF report pairs."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image as PILImage  # noqa: E402

from app.dda.geotiff_io import load_rgb_pil  # noqa: E402
from app.detection_config import get_load_max_side  # noqa: E402
import app.detection_engine as de  # noqa: E402

root = ROOT / "data/library_sources/central_delhi/Images"
pairs = [
    ("H43X2E2.tif", "0304.tif", "AI-Based Deep Learning"),
    ("Grid_54.tif", "H43X2E1.tif", "AI-Based Deep Learning"),
    ("1.tif", "2.tif", "Hybrid AI"),
]

for bname, aname, method in pairs:
    b = load_rgb_pil(root / bname, max_side=get_load_max_side())
    a = load_rgb_pil(root / aname, max_side=get_load_max_side())
    if b.size != a.size:
        a = a.resize(b.size, PILImage.Resampling.LANCZOS)
    _, _, stats, regions = de.run_detection(
        b, a, method=method, enable_registration=True,
        enable_normalization=True, detection_sensitivity=0.45,
        min_region_area=150,
        before_path=str(root / bname), after_path=str(root / aname),
    )
    gsd = de._CURRENT_GSD_MPP
    types = {}
    for r in regions:
        types[r["object_type"]] = types.get(r["object_type"], 0) + 1
    print(f"\n{bname} vs {aname} ({method})")
    print(f"  gsd={round(gsd, 4) if gsd else None}  change%={stats['change_percentage']:.2f}  regions={len(regions)}")
    print(f"  types: {types}")
    if gsd:
        small = sum(1 for r in regions if r["area"] * gsd * gsd < 45)
        print(f"  regions <45m2: {small}")
