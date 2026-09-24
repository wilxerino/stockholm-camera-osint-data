# Mosaic Stockholm camera data

Public, source-linked camera and ATM location data for Mosaic's Cameras tab. This repository contains data and deterministic update scripts; the Mosaic application source is separate.

The reviewed production files are `data/cameras.json`, `data/sources.json`, `data/atms.json`, and `data/manifest.json`. The manifest hashes each file. Every camera has source provenance, and public image/video feeds require explicit publisher documentation. ATM locations are separate from verified camera records. Unknown direction, FOV, range, and feed access remain unknown. A sourced direction may produce a clearly illustrative cone in the app; a measured cone requires sourced width and range.

Scheduled GitHub Actions inspect Trafiken.nu, mapped OpenStreetMap cameras and ATMs, and traffic-safety listings. Discovery writes candidates and reconciliation proposals to reviewable pull requests. It never promotes findings to production automatically. Research findings from ChatGPT use one candidate envelope per source: `{"schema_version":"1.0","source":"SOURCE_ID","sources":[...],"candidates":[...]}`. Keep `confidence` as `pending_review` until a human accepts it.

Validation:

```bash
python3 scripts/camera_data.py validate
python3 -m unittest discover -s tests -p 'test_*.py'
```

See `DATA-LICENSE.md` for OpenStreetMap attribution and database licensing. Public source links in the records may have their own terms. No camera images or videos are redistributed here. This dataset cannot represent every physical camera or ATM in Stockholm.
