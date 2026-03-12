"""
Email notification module.
Sends HTML-formatted detection reports via the manager's email API (HTTPS)
or SMTP (e.g. Gmail) when API URL is not set.
Credentials and API URL from environment variables.
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

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "templates" / "ChangeDetection.html"

# Manager's email API (multipart/form-data). When set, used instead of SMTP.
EMAIL_API_URL = os.environ.get(
    "EMAIL_API_URL",
    "https://emailservice.managemybusinessess.com/api/email/send",
)


def _smtp_settings():
    return {
        "host": os.environ.get("SMTP_HOST", "smtp.gmail.com"),
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "user": os.environ.get("SMTP_USER", "vedangofficeserver@gmail.com"),
        "password": os.environ.get("SMTP_PASS", ""),
    }


def _valid_email(email: str) -> bool:
    return bool(email and EMAIL_RE.match(email.strip()))


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

    if regions:
        html = html.replace("{{#regions}}", "").replace("{{/regions}}", "")
    else:
        html = re.sub(r"\{\{#regions\}\}.*?\{\{/regions\}\}", "", html, flags=re.DOTALL)

    return html


def _send_via_api(recipient: str, subject: str, html_body: str):
    """Send email via manager's API (POST multipart/form-data)."""
    if not EMAIL_API_URL or not EMAIL_API_URL.strip():
        return False, "EMAIL_API_URL is not set."
    url = EMAIL_API_URL.strip()
    try:
        import requests
        # API expects multipart/form-data: ToEmail, Subject, Body, FileName, AttachmentBase64
        files = {
            "ToEmail": (None, recipient),
            "Subject": (None, subject),
            "Body": (None, html_body),
            "FileName": (None, ""),
            "AttachmentBase64": (None, ""),
        }
        resp = requests.post(
            url,
            files=files,
            timeout=30,
            headers={"Accept": "*/*"},
        )
        if resp.status_code >= 200 and resp.status_code < 300:
            logger.info("Email API: sent to %s", recipient)
            return True, None
        msg = f"API returned {resp.status_code}"
        try:
            body = resp.text or resp.reason
            if body:
                msg = f"{msg}: {body[:200]}"
        except Exception:
            pass
        logger.warning("Email API failed: %s", msg)
        return False, msg
    except Exception as exc:
        logger.exception("Email API request failed: %s", exc)
        return False, f"{type(exc).__name__}: {exc}"


def _send_via_smtp(recipient: str, subject: str, html_body: str):
    """Send email via SMTP (e.g. Gmail)."""
    settings = _smtp_settings()
    smtp_user = settings["user"]
    smtp_pass = settings["password"]
    smtp_host = settings["host"]
    smtp_port = settings["port"]

    if not smtp_user or not smtp_pass:
        return False, "SMTP credentials are not configured on the server."

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = recipient
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, recipient, msg.as_string())
        logger.info("SMTP email sent to %s", recipient)
        return True, None
    except smtplib.SMTPAuthenticationError:
        logger.exception("SMTP authentication failed")
        return False, "SMTP authentication failed. Check the Gmail app password."
    except Exception as exc:
        logger.error(
            "Failed to send notification email to %s: %s: %s",
            recipient,
            type(exc).__name__,
            exc,
        )
        return False, f"{type(exc).__name__}: {exc}"


def _send_html_email(recipient: str, subject: str, html_body: str):
    """Send email: use manager's API if EMAIL_API_URL is set, else SMTP."""
    if not _valid_email(recipient):
        return False, "Enter a valid recipient email address."

    if EMAIL_API_URL and EMAIL_API_URL.strip():
        return _send_via_api(recipient, subject, html_body)
    return _send_via_smtp(recipient, subject, html_body)


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
):
    """Send a detection report email and return (success, error_message)."""
    html_body = build_email_body(
        title, method, zone, village, change_pct, changed_px, total_px, regions
    )
    subject = f"Change Detection Report — {title or 'Untitled run'}"
    return _send_html_email(recipient, subject, html_body)


def send_test_email(recipient: str):
    """Send a small test email to verify delivery."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html_body = f"""
    <html>
      <body style="font-family: Arial, sans-serif; color: #222;">
        <h2>AI Change Detection Email Test</h2>
        <p>This is a test email from the AI Change Detection application.</p>
        <p>If you received this, the email configuration is working.</p>
        <p><strong>Timestamp:</strong> {now}</p>
      </body>
    </html>
    """
    return _send_html_email(recipient, "AI Change Detection — Test Email", html_body)
