# Local Image Library

Place satellite / drone images here **by year**. The app reads directly from this folder — no web upload needed.

## Folder structure

```
library_sources/
  2024/
    your_image.tif
    optional_subfolder/
      another_image.tif
  2025/
    site_a.tif
  2026/
```

## Supported formats

- `.tif` / `.tiff` (GeoTIFF — preferred)
- `.png`, `.jpg`, `.jpeg` (for testing)

## How to use

1. Copy your images into the correct **year** folder:
   ```
   change_detection_webapp/library_sources/2025/your_image.tif
   change_detection_webapp/library_sources/2026/other_image.tif
   ```
2. Start the app locally: `python run.py` (DDA mode is enabled automatically).
3. Open **Image Library** → click **Refresh**.
4. Select **2025** or **2026** in the sidebar to filter by year.

> **Hugging Face dev Space:** large `.tif` files are **not** uploaded via git. Run locally, or copy files into the Space persistent `data/library_sources/` folder.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Images not showing | Use `python run.py` locally; click **Refresh** |
| Wrong page (simple upload UI) | Set `APP_MODE=dda` on HF, or run locally |
| Filename has spaces | Supported (e.g. `Grid no. 54_ORI-005.tif`) |
| Check scan | Open `/api/dda/local/debug` in browser |

## Large files

GeoTIFF files up to **2 GB** are supported when read from disk. Copy files via Explorer/Finder — much faster than browser upload.

## Custom location

Set environment variable `LOCAL_LIBRARY_ROOT` to use a different folder path.
