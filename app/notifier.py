"""
Email notification module.
Sends HTML-formatted detection reports via SMTP SSL.
Credentials are read from environment variables — never hardcoded.
"""
import logging
import os
import re
import smtplib
import ssl
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

logger = logging.getLogger(__name__)

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")

TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "templates" / "ChangeDetection.html"


def _load_template() -> str:
    """Read the HTML email template from disk."""
    if not TEMPLATE_PATH.exists():
        logger.error("Email template not found at %s", TEMPLATE_PATH)
        return "<p>Change Detection report — template file missing.</p>"
    return TEMPLATE_PATH.read_text(encoding="utf-8")


def _build_region_rows(regions: list) -> str:
    """Render region rows as HTML <tr> elements for the email template."""
    if not regions:
        return ""
    rows = []
    for r in regions[:20]:
        bg = "#f9f9fb" if r.get("id", 0) % 2 == 0 else "#ffffff"
        sub = r.get("subType") or "—"
        conf = f'{r.get("confidence", 0) * 100:.0f}%'
        area = f'{r.get("area", 0):,}'
        rows.append(
            f'<tr style="background:{bg};">'
            f'<td style="padding:6px 10px;">{r.get("id","")}</td>'
            f'<td style="padding:6px 10px;">{r.get("objectType","")}</td>'
            f'<td style="padding:6px 10px;">{sub}</td>'
            f'<td style="padding:6px 10px;">{conf}</td>'
            f'<td style="padding:6px 10px; text-align:right;">{area}</td>'
            f"</tr>"
        )
    return "\n".join(rows)


def build_email_body(
    title: str,
    method: str,
    zone: str,
    village: str,
    change_pct: float,
    changed_px: int,
    total_px: int,
    regions: list,
) -> str:
    """Populate the HTML template with detection results."""
    html = _load_template()
    location = ", ".join(filter(None, [village, zone])) or "—"
    region_rows = _build_region_rows(regions)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    replacements = {
        "{{title}}": title or "Untitled run",
        "{{method}}": method or "—",
        "{{location}}": location,
        "{{change_pct}}": f"{change_pct:.2f}",
        "{{changed_px}}": f"{changed_px:,}",
        "{{total_px}}": f"{total_px:,}",
        "{{regions_count}}": str(len(regions)),
        "{{region_rows}}": region_rows,
        "{{timestamp}}": now,
    }
    for key, val in replacements.items():
        html = html.replace(key, val)

    # Handle conditional regions block
    if regions:
        html = html.replace("{{#regions}}", "").replace("{{/regions}}", "")
    else:
        html = re.sub(r"\{\{#regions\}\}.*?\{\{/regions\}\}", "", html, flags=re.DOTALL)

    return html


def send_notification(
    recipient: str,
    title: str,
    method: str,
    zone: str,
    village: str,
    change_pct: float,
    changed_px: int,
    total_px: int,
    regions: list,
) -> bool:
    """
    Send detection report email to the recipient.
    Returns True on success, False on failure (never raises).
    """
    if not SMTP_PASS or not SMTP_USER:
        logger.warning("SMTP_USER or SMTP_PASS not set — skipping email notification")
        return False

    html_body = build_email_body(
        title, method, zone, village, change_pct, changed_px, total_px, regions
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Change Detection Report — {title or 'Untitled run'}"
    msg["From"] = SMTP_USER
    msg["To"] = recipient
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context) as server:
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, recipient, msg.as_string())
        logger.info("Notification email sent to %s", recipient)
        return True
    except Exception as e:
        logger.error("Failed to send notification email: %s", e)
        return False
