# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]
### Added
### Changed
### Removed

## 0.3.5 - 2023-05-20

Migrate off v2 transactions api.

## 0.3.4 - 2023-05-15

Relax dependencies to be compatible with home assistant, tighten requirements on boto3 for faster resolution.

## 0.3.3 - 2022-11-28

### Changed

- Improved debug logging.

## 0.3.2 - 2022-11-24

### Changed

- Fix to support `Installer` users.
- Update dependencies.
- Release via GitHub rather than manually.

## 0.3.1 - 2022-10-27

### Changed

- `set_charger_availability` switched to Evnex API V3.
- Marked `get_charge_point_detail` as deprecated in favor of `get_charge_point_detail_v3`


## 0.3.0 - 2022-10-26

### Added

Exposed extra functions to configure load and charging schedules and disable/enable chargers.

### Changed

`EvnexChargeProfileSegment` types are now assumed to be `int`.
