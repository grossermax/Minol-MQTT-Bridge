# Changelog

## [1.4.0](https://github.com/grossermax/Minol-MQTT-Bridge/compare/v1.3.0...v1.4.0) (2026-08-25)


### Features

* expose evaluated consumption for Home Assistant statistics (instead of raw consumption) ([f182438](https://github.com/grossermax/Minol-MQTT-Bridge/commit/f18243807eca114af14ecdfb3d0a1961db52465d))
* extend monthly history to include evaluated consumption and factors; and rename consumption attributes to total_consumption and total_consumption_evaluated for clarity ([ae253c0](https://github.com/grossermax/Minol-MQTT-Bridge/commit/ae253c0aa4833905bb296dddd38a9f2a2e918219))


### Bug Fixes

* remove unit_raw attribute from heating data and documentation ([aaa84c2](https://github.com/grossermax/Minol-MQTT-Bridge/commit/aaa84c2fb56e58c7aa06d292201f5623639f39cf))


### Miscellaneous Chores

* localize release date and sync add-on changelog for Home Assistant ([5f576a0](https://github.com/grossermax/Minol-MQTT-Bridge/commit/5f576a021c736a697d540a37c514b13eb93cbf56))


### Documentation

* update sensor naming for consistency in README and missing attributes per room ([4a6148a](https://github.com/grossermax/Minol-MQTT-Bridge/commit/4a6148aa0c28962ced1699943fcb457cba2199f3))

## [1.3.0](https://github.com/grossermax/Minol-MQTT-Bridge/compare/v1.2.2...v1.3.0) (2026-08-24)


### Features

* update entity names for clarity and consistency ([#20](https://github.com/grossermax/Minol-MQTT-Bridge/issues/20)) ([f6ff02c](https://github.com/grossermax/Minol-MQTT-Bridge/commit/f6ff02c9e1237b53d0b29b118a46e87eefb64139))


### Bug Fixes

* add unit_raw to "Minol Heating Total" ([04b3341](https://github.com/grossermax/Minol-MQTT-Bridge/commit/04b3341c227c417a0d569af2c2caba2f287b7c8d))

## [1.2.2](https://github.com/grossermax/Minol-MQTT-Bridge/compare/v1.2.1...v1.2.2) (2026-08-24)


### Miscellaneous Chores

* make python-dotenv dev-only and trim docker build context ([#18](https://github.com/grossermax/Minol-MQTT-Bridge/issues/18)) ([7897035](https://github.com/grossermax/Minol-MQTT-Bridge/commit/7897035481a3f095a36a9141e431d26259d0603b))

## [1.2.1](https://github.com/grossermax/Minol-MQTT-Bridge/compare/v1.2.0...v1.2.1) (2026-08-24)


### Bug Fixes

* reduce image size and remove dev-only runtime dependencies ([#14](https://github.com/grossermax/Minol-MQTT-Bridge/issues/14)) ([c218606](https://github.com/grossermax/Minol-MQTT-Bridge/commit/c218606c5b759bbd8329c3d207decb9cb834be33))


### Miscellaneous Chores

* remove obsolete release-please bootstrap-sha now that v1.2.0 tag exists ([#13](https://github.com/grossermax/Minol-MQTT-Bridge/issues/13)) ([e74007d](https://github.com/grossermax/Minol-MQTT-Bridge/commit/e74007d15246bb3aa810fe97596bacb6bbfdfedc))
* update changelog sections in release-please configuration ([#16](https://github.com/grossermax/Minol-MQTT-Bridge/issues/16)) ([0f7fce7](https://github.com/grossermax/Minol-MQTT-Bridge/commit/0f7fce759aca4d5045ed95deb7a95ae6bd9cb289))
* update changelog sections in release-please configuration ([#17](https://github.com/grossermax/Minol-MQTT-Bridge/issues/17)) ([c812e70](https://github.com/grossermax/Minol-MQTT-Bridge/commit/c812e707ec1afe07159dca34a60ad48a71e1005f))


### Continuous Integration

* sync add-on changelog via release PR branch to satisfy branch protection ([#11](https://github.com/grossermax/Minol-MQTT-Bridge/issues/11)) ([5d468ef](https://github.com/grossermax/Minol-MQTT-Bridge/commit/5d468ef34be7da99f5700f29f568c33e0ec1ed25))
* sync add-on changelog via release PR branch to satisfy branch protection ([#12](https://github.com/grossermax/Minol-MQTT-Bridge/issues/12)) ([09af028](https://github.com/grossermax/Minol-MQTT-Bridge/commit/09af0282edfe13657deb3989d7418a8843692920))

## [1.2.0](https://github.com/grossermax/Minol-MQTT-Bridge/compare/v1.1.1...v1.2.0) (2026-08-24)

### Features

* implement browser-less Azure AD B2C authentication using requests and remove Playwright
  ([944ce39](https://github.com/grossermax/Minol-MQTT-Bridge/commit/944ce399eca357ea53b4ddf79860668a158d8bf4))

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
