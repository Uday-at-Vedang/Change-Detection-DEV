"""One-shot helper to clear the login-failure counter for a given IP.

Usage:  python scripts/reset_login_throttle.py [ip]
Default IP is 127.0.0.1 (local dev). Deletes recent `login_failed` rows so
the IP throttle check_ip_throttle() no longer flags this address.
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

from app.database import SessionLocal  # noqa: E402
from app.models import LoginHistory  # noqa: E402
from app.dda.rbac.models import Role, Module, RolePermission, MenuItem  # noqa: E402,F401 — needed for User.role_obj mapper resolution


def main() -> None:
    ip = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    db = SessionLocal()
    try:
        deleted = (
            db.query(LoginHistory)
            .filter(
                LoginHistory.ip_address == ip,
                LoginHistory.event_type.in_(("login_failed", "login_blocked", "otp_request_failed")),
            )
            .delete(synchronize_session=False)
        )
        db.commit()
        print(f"Cleared {deleted} throttle-relevant rows for IP {ip}.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
