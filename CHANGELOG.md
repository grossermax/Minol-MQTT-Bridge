# Changelog

## [1.2.1] - 2026-08-24

### Changed

- Re-added `black` to `requirements.txt`.
- Updated changelog handling for Home Assistant: full history stays in the root `CHANGELOG.md`, while
  `minol_mqtt_bridge/CHANGELOG.md` only contains the latest release notes.

## [1.2.0] - 2026-08-24

### Added

- **Browser-less login**: the Azure AD B2C (SAML) sign-in is reproduced purely with `requests`, so **no browser is
  needed**. This shrinks the image from ~0.9 GB to ~0.16 GB and brings Home Assistant backups back down to roughly their
  original size.
- `total_consumption_evaluated` attribute on the `heizkostenverteiler_minol_total` sensor: sum of the per-room
  factor-weighted (`consumptionBew`) values, consistent with the individual per-room sensors which already expose
  `consumption_evaluated`.

### Removed

- Removed Playwright/Chromium entirely (dependency, Docker install and code). Authentication no longer requires a
  browser.

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
