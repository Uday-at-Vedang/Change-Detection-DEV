import json
import math
import sqlite3
from pathlib import Path
import shutil

ROOT = Path(".")
shutil.copy(
    "data/overlays/7_d539e8854d9248d6a26f21400da21d8b.png",
    "data/delhi_cd/friday_drone_report_fix/run73_overlay.png",
)
shutil.copy(
    "data/overlays/7_15fae0bfc5734560bafc62fe7526d0cd.png",
    "data/delhi_cd/friday_drone_report_fix/run61_overlay.png",
)

con = sqlite3.connect("data/satellite_app.db")


def regs(rid):
    return json.loads(
        con.execute("select regions_json from detection_runs where id=?", (rid,)).fetchone()[0]
    )


r61, r73 = regs(61), regs(73)
print("61 majors", [(r["id"], r["area"], r.get("objectType")) for r in r61 if r.get("severity") == "major"])
print("73 majors", [(r["id"], r["area"], r.get("objectType")) for r in r73 if r.get("severity") == "major"])
for a in r61[:7]:
    c = a["center"]
    cx = c["x"] if isinstance(c, dict) else c[0]
    cy = c["y"] if isinstance(c, dict) else c[1]
    best = None
    for b in r73:
        c2 = b["center"]
        bx = c2["x"] if isinstance(c2, dict) else c2[0]
        by = c2["y"] if isinstance(c2, dict) else c2[1]
        d = math.hypot(cx - bx, cy - by)
        if best is None or d < best[0]:
            best = (d, b)
    print(
        f"61#{a['id']} area={a['area']} -> nearest73 "
        f"#{best[1]['id']} area={best[1]['area']} dist={best[0]:.0f} "
        f"ratio={best[1]['area']/max(a['area'],1):.2f}"
    )
