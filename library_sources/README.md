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

1. Copy your images into the correct **year** folder (e.g. `library_sources/2025/`).
2. Run the app (`python run.py` or open the dev Space).
3. Open **Image Library** → click **Refresh** if you added files while the app was running.
4. Select a year in the sidebar to view images.

## Large files

GeoTIFF files up to **2 GB** are supported when read from disk. Copy files via Explorer/Finder — much faster than browser upload.

## Custom location

Set environment variable `LOCAL_LIBRARY_ROOT` to use a different folder path.
