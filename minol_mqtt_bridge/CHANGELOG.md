# Changelog

## [1.1.1] - 2026-08-23

### Changed

- Switched the container base image from the full Playwright image to
  `python:3.14-slim` and install **only** the lightweight Chromium headless
  shell. This shrinks the add-on image from ~1.7 GB to ~0.9 GB and keeps Home
  Assistant backups much smaller.
- `minol_connector` now launches the `chromium-headless-shell` build (with an
  automatic fallback to the full Chromium for local development).
- Removed unused Chromium fonts and caches from the image.
- Removed the dev-only `black` dependency from `requirements.txt`.
- Updated `README.md` (memory note, base image, "How It Works") to match the
  slimmer runtime.

## [1.1.0] - 2026-08-22

### Added

- Selectable consumption types (`HEIZUNG`, `WARMWASSER`, `KALTWASSER`).
- True per-month and per-room consumption breakdown.
- Bridge availability plus persistent state with reset protection for
  `total_increasing` sensors.
