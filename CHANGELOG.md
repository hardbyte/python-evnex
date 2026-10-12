# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]
### Added
- `Evnex(timeout=...)` sets the timeout, in seconds, for the cloud API's own
  endpoints (default 15). It is passed per request, so it always overrides the
  httpx client's timeout; a shared client such as Home Assistant's cannot
  change it. Commands relayed to the charge point, and methods that take a
  `timeout` argument, keep their own.
### Changed
- Read timeouts are no longer retried. A timed-out request reached the API and
  went unanswered; retrying it five times with backoff could occupy several
  minutes, longer than a typical poller's whole interval. Callers now see the
  `httpx.ReadTimeout` immediately and decide when to ask again.
- The default request timeout is 15 seconds, down from 30. Most cloud reads
  answer in under 0.2s, but `get_org_charge_points` measures 3.8s and
  `get_charge_point_sessions` 2.9s, so the old httpx default of 5s left those
  two barely any headroom -- the likely source of the intermittent
  `ReadTimeout` on `get_org_charge_points` under Home Assistant, which polls
  them 288 times a day. The `commands/*` endpoints relay to the charge point
  over OCPP and now share a 30s `COMMAND_TIMEOUT`: `get-override` was capped at
  15s, inside the 15-20s round trip a healthy charger normally takes, so it
  reported ONLINE chargers as offline.
- Restored the `timeout` parameter on `get_charge_point_energy_meter_reading`,
  dropped in 0.7.1 when the blanket 30s default replaced it.
### Removed

## 0.4.5 - 2024-08-21

- Removed obsolete /v3/ from paths

## 0.4.4 - 2024-06-8

- fix for get_org_charge_points by @drc38 in #62

## 0.3.9 - 2024-01-06

Added shims to support Pydantic v1 and v2.

## 0.3.8 - 2024-01-05

Update dependencies to work with homeassistant 2024.

## 0.3.6 - 2023-11-06

Dependency updates.

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
