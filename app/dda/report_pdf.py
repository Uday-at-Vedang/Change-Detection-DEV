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


def build_report_dict(run: DetectionRun, *, include_overlay_b64: bool = False, regions: Optional[List[dict]] = None) -> Dict[str, Any]:
    if regions is None:
        regions = json.loads(run.regions_json or "[]")
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
        "afterFullUrl": f"/api/overlay/{run.after_full_path}" if getattr(run, "after_full_path", None) else None,
        "createdAt": _isoformat_ist(run.created_at),
        "pdfUrl": f"/api/dda/reports/{run.id}/pdf",
    }
    if include_overlay_b64 and run.overlay_path:
        overlay_file = DATA_DIR / run.overlay_path
        if overlay_file.exists():
            import base64
            payload["overlayBase64Png"] = base64.b64encode(overlay_file.read_bytes()).decode("utf-8")
    return payload


def generate_report_pdf(run: DetectionRun, *, regions: Optional[List[dict]] = None) -> tuple[bytes, str]:
    """Build PDF bytes and suggested download filename."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Image as RLImage
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    from .geo_regions import region_lat_lng

    if regions is None:
        regions = json.loads(run.regions_json or "[]")
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
        # Plain strings don't wrap in reportlab Tables — a long value just
        # overflows visually into the next cell instead of breaking onto a
        # second line (this is why "DDA type"/"Classification" text used to
        # run into the "Conf." column). Wrap the long-text columns in
        # Paragraph so they wrap within their own column width instead.
        cell_style = ParagraphStyle("TableCell", parent=styles["Normal"], fontSize=8, leading=9.5)

        def cell(text: str) -> Paragraph:
            return Paragraph(text, cell_style)

        table_data = [["#", "Severity", "DDA type", "Classification", "Conf.", "Area (m²)", "Lat", "Lng", "Review"]]
        for r in regions[:50]:
            lat, lng = region_lat_lng(r)
            if r.get("areaSqM") is not None:
                # Unit lives in the header ("Area (m²)") now, not repeated
                # every row — matches the live review table's convention.
                area_txt = f'{r["areaSqM"]:,.1f}'
            elif r.get("polygonAreaPx") is not None:
                # Different unit than the header claims (no georeferencing
                # available for this region) — keep it explicit inline so
                # it isn't mistaken for m².
                area_txt = f'{int(round(r["polygonAreaPx"])):,} px'
            else:
                area_txt = f'{r.get("area", 0):,} px'
            severity_txt = (r.get("severity") or "Minor").strip().capitalize()
            table_data.append([
                str(r.get("id", "")),
                severity_txt,
                cell(r.get("ddaChangeType") or r.get("objectType") or "—"),
                cell(r.get("internalObjectType") or r.get("objectType") or "—"),
                f'{(r.get("confidence", 0) or 0) * 100:.0f}%',
                area_txt,
                f"{lat:.5f}" if lat is not None else "—",
                f"{lng:.5f}" if lng is not None else "—",
                r.get("reviewStatus") or "pending",
            ])
        tbl = Table(table_data, repeatRows=1, colWidths=[18, 40, 68, 82, 30, 52, 44, 44, 46])
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
