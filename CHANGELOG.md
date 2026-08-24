# Changelog

## [1.1.1] - 2026-08-23

### Changed

- Switched the container base image to `python:3.14-slim` and slimmed down the build to keep the add-on image and Home
  Assistant backups smaller.
- Removed the dev-only `black` dependency from `requirements.txt`.
- Added a `.dockerignore` to keep the build context small.
- Updated `README.md` (memory note, base image, "How It Works").

## [1.1.0] - 2026-08-22

### Added

- Selectable consumption types (`HEIZUNG`, `WARMWASSER`, `KALTWASSER`).
- True per-month and per-room consumption breakdown.
- Bridge availability plus persistent state with reset protection for
  `total_increasing` sensors.
