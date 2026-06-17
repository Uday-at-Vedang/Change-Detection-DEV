"""PDF export for DDA detection reports (FR-05)."""
from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..database import DATA_DIR
from ..models import DetectionRun
from .detect_service import _isoformat_ist


def _safe_filename(title: str, run_id: int) -> str:
    base = "".join(c if c.isalnum() or c in " -_" else "_" for c in (title or "report"))
    base = base.strip().replace(" ", "_")[:60] or "report"
    return f"DDA_Report_{run_id}_{base}.pdf"


def build_report_dict(run: DetectionRun, *, include_overlay_b64: bool = False) -> Dict[str, Any]:
    regions: List[dict] = json.loads(run.regions_json or "[]")
    payload: Dict[str, Any] = {
        "id": run.id,
        "title": run.title,
        "method": run.method,
        "zone": run.zone or "",
        "village": run.village or "",
        "statistics": {
            "totalPixels": run.total_pixels,
            "changedPixels": run.changed_pixels,
            "unchangedPixels": run.total_pixels - run.changed_pixels,
            "changePercentage": run.change_percentage,
        },
        "regions": regions,
        "regionsCount": run.regions_count,
        "overlayUrl": f"/api/overlay/{run.overlay_path}" if run.overlay_path else None,
        "beforeFullUrl": f"/api/overlay/{run.before_full_path}" if run.before_full_path else None,
        "beforeThumbUrl": f"/api/overlay/{run.before_thumb_path}" if run.before_thumb_path else None,
        "afterThumbUrl": f"/api/overlay/{run.after_thumb_path}" if run.after_thumb_path else None,
        "createdAt": _isoformat_ist(run.created_at),
        "pdfUrl": f"/api/dda/reports/{run.id}/pdf",
    }
    if include_overlay_b64 and run.overlay_path:
        overlay_file = DATA_DIR / run.overlay_path
        if overlay_file.exists():
            import base64
            payload["overlayBase64Png"] = base64.b64encode(overlay_file.read_bytes()).decode("utf-8")
    return payload


def generate_report_pdf(run: DetectionRun) -> tuple[bytes, str]:
    """Build PDF bytes and suggested download filename."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Image as RLImage
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    regions: List[dict] = json.loads(run.regions_json or "[]")
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("ReportTitle", parent=styles["Heading1"], fontSize=16, spaceAfter=8)
    sub_style = ParagraphStyle("ReportSub", parent=styles["Normal"], fontSize=10, textColor=colors.grey)
    body = styles["Normal"]

    location = ", ".join(filter(None, [run.village, run.zone])) or "—"
    story = [
        Paragraph("DDA Change Detection Report", title_style),
        Paragraph(run.title or f"Run #{run.id}", styles["Heading2"]),
        Paragraph(f"Generated {_isoformat_ist(run.created_at)} IST", sub_style),
        Spacer(1, 8),
        Paragraph(f"<b>Method:</b> {run.method or '—'}", body),
        Paragraph(f"<b>Location:</b> {location}", body),
        Paragraph(f"<b>Change:</b> {run.change_percentage:.2f}% ({run.changed_pixels:,} / {run.total_pixels:,} px)", body),
        Paragraph(f"<b>Regions detected:</b> {run.regions_count}", body),
        Spacer(1, 10),
    ]

    overlay_path = run.overlay_path
    if overlay_path:
        overlay_file = DATA_DIR / overlay_path
        if overlay_file.exists():
            try:
                from PIL import Image as PILImage
                with PILImage.open(overlay_file) as im:
                    im = im.convert("RGB")
                    max_w = 160 * mm
                    ratio = min(1.0, max_w / im.width)
                    w, h = int(im.width * ratio), int(im.height * ratio)
                    thumb = im.resize((w, h), PILImage.Resampling.LANCZOS)
                    img_buf = BytesIO()
                    thumb.save(img_buf, format="JPEG", quality=85)
                    img_buf.seek(0)
                    story.append(Paragraph("Change overlay", styles["Heading3"]))
                    story.append(RLImage(img_buf, width=w, height=h))
                    story.append(Spacer(1, 10))
            except Exception:
                pass

    story.append(Paragraph("Detected regions", styles["Heading3"]))
    if regions:
        table_data = [["#", "DDA type", "Internal type", "Conf.", "Area (px)", "Lat", "Lng"]]
        for r in regions[:50]:
            lat = r.get("latitude") or r.get("lat")
            lng = r.get("longitude") or r.get("lng")
            table_data.append([
                str(r.get("id", "")),
                r.get("ddaChangeType") or r.get("objectType") or "—",
                r.get("internalObjectType") or r.get("objectType") or "—",
                f'{(r.get("confidence", 0) or 0) * 100:.0f}%',
                f'{r.get("area", 0):,}',
                f"{lat:.5f}" if lat is not None else "—",
                f"{lng:.5f}" if lng is not None else "—",
            ])
        tbl = Table(table_data, repeatRows=1, colWidths=[22, 72, 72, 36, 52, 48, 48])
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2e33c5")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f9f9fb")]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(tbl)
        if len(regions) > 50:
            story.append(Spacer(1, 6))
            story.append(Paragraph(f"Showing first 50 of {len(regions)} regions.", sub_style))
    else:
        story.append(Paragraph("No change regions detected.", body))

    doc.build(story)
    return buf.getvalue(), _safe_filename(run.title or "", run.id)
