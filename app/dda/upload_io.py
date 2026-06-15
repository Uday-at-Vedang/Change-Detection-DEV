"""Stream large uploads to disk without loading into memory."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import HTTPException, UploadFile

logger = logging.getLogger(__name__)

CHUNK_SIZE = 8 * 1024 * 1024  # 8 MB


async def stream_upload_to_file(
    upload: UploadFile,
    dest_path: Path,
    max_bytes: int,
) -> int:
    """
    Write upload body to dest_path in chunks. Returns total bytes written.
    Removes partial file on failure or oversize.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with dest_path.open("wb") as out:
            while True:
                chunk = await upload.read(CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    max_mb = max_bytes // (1024 * 1024)
                    limit = f"{max_mb // 1024} GB" if max_mb >= 1024 else f"{max_mb} MB"
                    raise HTTPException(
                        status_code=400,
                        detail=f"File too large (max {limit})",
                    )
                out.write(chunk)
    except HTTPException:
        dest_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        dest_path.unlink(missing_ok=True)
        logger.exception("Upload stream failed")
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}") from exc

    if total == 0:
        dest_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="File is empty")
    return total
