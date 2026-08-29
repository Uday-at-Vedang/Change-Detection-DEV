"""List users in the local DB so we can diagnose sign-in mismatches.

Usage:  python scripts/show_users.py [substring]
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

os.environ.setdefault("APP_MODE", "dda")

from sqlalchemy import text as sa_text  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import LoginHistory  # noqa: E402,F401 — register mapper
from app.dda.rbac.models import Role, Module, RolePermission, MenuItem  # noqa: E402,F401


def main() -> None:
    substr = (sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()
    db = SessionLocal()
    try:
        rows = db.execute(sa_text(
            "SELECT id, email, full_name, phone, role, created_at FROM users ORDER BY id"
        )).fetchall()
        matches = [r for r in rows if not substr or substr in (r[1] or "").lower()]
        print(f"Total users: {len(rows)}    Matching {'*' if not substr else repr(substr)}: {len(matches)}")
        print("-" * 90)
        for r in matches:
            print(f"#{r[0]:>3}  {r[1]!r:<40}  phone={r[3]!r}  role={r[4]!r}  fullName={r[2]!r}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
