"""Tests for the CLI resource commands: parser wiring, charge-point
resolution, per-command behaviour against mocked API responses, and JSON
output purity.

HTTP is mocked with respx; sign-in is replaced with a resumed offline auth so
no credentials or network are needed. The payloads below validate against the
real pydantic response models (see test_fixtures_validate_against_models).
"""

import asyncio
import json

import httpx
import pytest
import respx

from evnex.cli import _resources, build_parser
from evnex.cli._resources import _match_charge_point, _resolve_one
from evnex.schema.charge_points import EvnexGetChargePointsResponse
from evnex.schema.org import EvnexGetOrgInsights
from evnex.schema.user import EvnexGetUserResponse
from evnex.schema.v3.charge_points import (
    EvnexChargePointDetail as EvnexChargePointDetailV3,
)
from evnex.schema.v3.charge_points import EvnexGetChargePointSessionsResponse
from evnex.schema.v3.generic import EvnexV3APIResponse

BASE = "https://client-api.evnex.io"
USER_URL = f"{BASE}/v2/apps/user"
CP_URL = f"{BASE}/v2/apps/organisations/org-0000/charge-points"
DETAIL_URL = f"{BASE}/charge-points/cp-0000001"
SESSIONS_URL = f"{BASE}/charge-points/cp-0000001/sessions"
INSIGHTS_URL = f"{BASE}/organisations/org-0000/summary/insights"
OVERRIDE_URL = f"{BASE}/charge-points/cp-0000001/commands/set-override"
STOP_URL = (
    f"{BASE}/v2/apps/organisations/org-0000"
    "/charge-points/cp-0000001/commands/remote-stop-transaction"
)

USER_PAYLOAD = {
    "data": {
        "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "createdDate": "2024-01-01T00:00:00Z",
        "updatedDate": "2024-01-01T00:00:00Z",
        "name": "Test User",
        "email": "user@example.com",
        "organisations": [
            {
                "id": "org-0000",
                "isDefault": True,
                "role": 1,
                "createdDate": "2024-01-01T00:00:00Z",
                "name": "Test Org",
                "slug": "test-org",
                "tier": 1,
                "updatedDate": "2024-01-01T00:00:00Z",
            }
        ],
        "type": "User",
    }
}


def _charge_point_item(cp_id, name, serial):
    return {
        "id": cp_id,
        "createdDate": "2024-01-01T00:00:00Z",
        "updatedDate": "2024-06-01T00:00:00Z",
        "networkStatusUpdatedDate": "2024-06-01T00:00:00Z",
        "name": name,
        "ocppChargePointId": serial,
        "serial": serial,
        "networkStatus": "ONLINE",
        "location": {
            "id": "loc-0000001",
            "name": "Home",
            "createdDate": "2024-01-01T00:00:00Z",
            "updatedDate": "2024-01-01T00:00:00Z",
            "address": {
                "address1": "1 Test Street",
                "city": "Wellington",
                "postCode": "6011",
                "country": "NZ",
            },
            "coordinates": {"latitude": -41.2865, "longitude": 174.7762},
            "chargePointCount": 1,
        },
        "details": {"model": "E2", "vendor": "Evnex", "firmware": "1.2.3"},
        "connectors": [
            {
                "powerType": "AC_1_PHASE",
                "connectorId": "1",
                "evseId": "1",
                "updatedDate": "2024-06-01T00:00:00Z",
                "connectorType": "TYPE_2_SOCKET",
                "amperage": 32,
                "voltage": 230,
                "connectorFormat": "CABLE",
                "ocppStatus": "AVAILABLE",
                "status": "AVAILABLE",
                "ocppCode": "NoError",
                "meter": {
                    "powerType": "AC_1_PHASE",
                    "updatedDate": "2024-06-01T00:00:00Z",
                    "power": 0,
                    "register": 12345.6,
                    "frequency": 50,
                },
            }
        ],
        "lastHeard": "2024-06-01T00:00:00Z",
        "maxCurrent": 32,
        "tokenRequired": False,
        "needsRegistrationInformation": False,
    }


CHARGE_POINTS_PAYLOAD = {
    "data": {"items": [_charge_point_item("cp-0000001", "Garage Charger", "SN0000001")]}
}

TWO_CHARGE_POINTS_PAYLOAD = {
    "data": {
        "items": [
            _charge_point_item("cp-0000001", "Garage Charger", "SN0000001"),
            _charge_point_item("cp-0000002", "Driveway Charger", "SN0000002"),
        ]
    }
}

DETAIL_V3_PAYLOAD = {
    "data": {
        "id": "cp-0000001",
        "type": "chargePoint",
        "attributes": {
            "connectors": [
                {
                    "evseId": "1",
                    "connectorFormat": "CABLE",
                    "connectorType": "TYPE_2_SOCKET",
                    "ocppStatus": "CHARGING",
                    "powerType": "AC_1_PHASE",
                    "connectorId": "1",
                    "ocppCode": "CHARGING",
                    "updatedDate": "2024-06-01T00:00:00Z",
                    "meter": {
                        "currentL1": 16,
                        "frequency": 50,
                        "power": 3600,
                        "register": 12345.6,
                        "supplyActivePower": 400,
                        "updatedDate": "2024-06-01T00:00:00Z",
                        "voltageL1N": 230,
                    },
                    "maxVoltage": 230,
                    "maxAmperage": 32,
                }
            ],
            "createdDate": "2024-01-01T00:00:00Z",
            "electricityCost": {
                "currency": "NZD",
                "tariffs": [{"start": 0, "rate": 0.28, "type": "Flat"}],
                "tariffType": "Flat",
                "cost": 0.28,
            },
            "firmware": "1.2.3",
            "maxCurrent": 32,
            "model": "E2",
            "name": "Garage Charger",
            "networkStatus": "ONLINE",
            "networkStatusUpdatedDate": "2024-06-01T00:00:00Z",
            "ocppChargePointId": "SN0000001",
            "profiles": {
                "chargeSchedule": {
                    "enabled": True,
                    "chargingSchedulePeriods": [
                        {"limit": 32, "startPeriod": 0},
                        {"limit": 0, "startPeriod": 79200},
                    ],
                }
            },
            "serial": "SN0000001",
            "timeZone": "Pacific/Auckland",
            "tokenRequired": False,
            "updatedDate": "2024-06-01T00:00:00Z",
            "vendor": "Evnex",
        },
        "relationships": {
            "chargePoint": None,
            "location": {"data": {"id": "loc-0000001", "type": "location"}},
            "organisation": {"data": {"id": "org-0000", "type": "organisation"}},
        },
    },
    "included": None,
}

SESSIONS_PAYLOAD = {
    "data": [
        {
            "id": "session-0000001",
            "type": "session",
            "attributes": {
                "connectorId": "1",
                "createdDate": "2024-06-02T08:00:00Z",
                "evseId": "1",
                "sessionStatus": "InProgress",
                "startDate": "2024-06-02T08:00:00Z",
                "updatedDate": "2024-06-02T08:30:00Z",
                "endDate": None,
                "totalPowerUsage": 3500,
                "totalCost": None,
            },
        },
        {
            "id": "session-0000002",
            "type": "session",
            "attributes": {
                "connectorId": "1",
                "createdDate": "2024-06-01T08:00:00Z",
                "evseId": "1",
                "sessionStatus": "Completed",
                "startDate": "2024-06-01T08:00:00Z",
                "updatedDate": "2024-06-01T09:00:00Z",
                "endDate": "2024-06-01T09:00:00Z",
                "totalPowerUsage": 7000,
                "totalCost": {"currency": "NZD", "amount": 1.96, "distribution": None},
            },
        },
    ]
}

INSIGHTS_PAYLOAD = {
    "data": [
        {
            "attributes": {
                "carbonOffset": 1.1,
                "carbonUsage": 0.9,
                "cost": {"currency": "NZD", "cost": 1.5},
                "duration": 3600,
                "powerUsage": 1000,
                "sessions": 1,
                "startDate": "2024-06-10T00:00:00Z",
            }
        },
        {
            "attributes": {
                "carbonOffset": 1.1,
                "carbonUsage": 0.9,
                "cost": {"currency": "NZD", "cost": 2.5},
                "duration": 7200,
                "powerUsage": 2000,
                "sessions": 2,
                "startDate": "2024-06-11T00:00:00Z",
            }
        },
    ]
}


@pytest.fixture
def cli(resumed_auth, monkeypatch):
    """Patch sign-in so resource handlers use the offline resumed session."""

    async def fake_signed_in(args):
        return resumed_auth

    monkeypatch.setattr("evnex.cli._resources.signed_in_auth", fake_signed_in)
    return resumed_auth


async def run(argv):
    """Parse argv and invoke the resolved leaf handler.

    Parsing happens in a worker thread because build_parser() reads package
    metadata from disk; production parses before entering the event loop.
    """
    args = await asyncio.to_thread(lambda: build_parser().parse_args(argv))
    await args.func(args)


def _charge_points(payload):
    return EvnexGetChargePointsResponse.model_validate(payload).data.items


# --- Fixture fidelity -----------------------------------------------------


def test_fixtures_validate_against_models():
    EvnexGetUserResponse.model_validate(USER_PAYLOAD)
    EvnexGetChargePointsResponse.model_validate(CHARGE_POINTS_PAYLOAD)
    detail = EvnexV3APIResponse[EvnexChargePointDetailV3].model_validate(
        DETAIL_V3_PAYLOAD
    )
    # The JSON key is 'register'; the python attribute is raw_register
    assert detail.data.attributes.connectors[0].meter.raw_register == 12345.6
    sessions = EvnexGetChargePointSessionsResponse.model_validate(SESSIONS_PAYLOAD)
    assert sessions.data[0].attributes.endDate is None
    insights = EvnexGetOrgInsights.model_validate(INSIGHTS_PAYLOAD)
    assert insights.data[0].attributes.sessions == 1


# --- Parser wiring --------------------------------------------------------


@pytest.mark.parametrize(
    "func_name, argv",
    [
        ("cmd_live_status", ["status"]),
        ("cmd_live_status", ["status", "--charge-point", "cp-1", "--json"]),
        ("cmd_charge_points_list", ["charge-points", "list"]),
        ("cmd_charge_points_list", ["charge-points", "list", "--json"]),
        ("cmd_charge_points_show", ["charge-points", "show"]),
        ("cmd_charge_points_show", ["charge-points", "show", "cp-1", "--json"]),
        ("cmd_sessions_list", ["sessions", "list"]),
        (
            "cmd_sessions_list",
            ["sessions", "list", "--charge-point", "cp-1", "--limit", "5", "--json"],
        ),
        ("cmd_insights", ["insights"]),
        ("cmd_insights", ["insights", "--days", "14", "--json"]),
        ("cmd_charge_now", ["charge", "now", "--charge-point", "cp-1"]),
        ("cmd_charge_auto", ["charge", "auto"]),
        ("cmd_charge_stop", ["charge", "stop", "--yes"]),
        ("cmd_charge_stop", ["charge", "stop", "-y"]),
        ("cmd_schedule_show", ["schedule", "show"]),
        ("cmd_schedule_show", ["schedule", "show", "--charge-point", "cp-1", "--json"]),
    ],
)
def test_resource_leaf_dispatch(func_name, argv):
    args = build_parser().parse_args(argv)
    assert args.func is getattr(_resources, func_name)


def test_shared_flags_accepted_in_trailing_position():
    args = build_parser().parse_args(
        ["status", "--json", "--otp", "123456", "--token-cache", "/tmp/tokens.json"]
    )
    assert args.json is True
    assert args.otp == "123456"
    assert str(args.token_cache) == "/tmp/tokens.json"


def test_sessions_limit_defaults_to_ten():
    args = build_parser().parse_args(["sessions", "list"])
    assert args.limit == 10


def test_insights_days_defaults_to_seven():
    args = build_parser().parse_args(["insights"])
    assert args.days == 7


def test_insights_rejects_unsupported_days():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["insights", "--days", "3"])


# --- Charge-point resolution ----------------------------------------------


def test_resolve_single_charge_point_by_default():
    charge_points = _charge_points(CHARGE_POINTS_PAYLOAD)
    assert _resolve_one(charge_points, None).id == "cp-0000001"


def test_resolve_ambiguous_default_exits_2(capsys):
    charge_points = _charge_points(TWO_CHARGE_POINTS_PAYLOAD)
    with pytest.raises(SystemExit) as exc:
        _resolve_one(charge_points, None)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "Select a charge point" in err
    assert "cp-0000001" in err and "cp-0000002" in err


def test_resolve_prefix_match_by_name():
    charge_points = _charge_points(TWO_CHARGE_POINTS_PAYLOAD)
    assert _resolve_one(charge_points, "garage").name == "Garage Charger"


def test_resolve_match_by_serial():
    charge_points = _charge_points(TWO_CHARGE_POINTS_PAYLOAD)
    assert _match_charge_point(charge_points, "sn0000002").id == "cp-0000002"


def test_resolve_exact_id_wins():
    charge_points = _charge_points(TWO_CHARGE_POINTS_PAYLOAD)
    assert _match_charge_point(charge_points, "cp-0000002").id == "cp-0000002"


def test_resolve_ambiguous_selector_exits_2(capsys):
    charge_points = _charge_points(TWO_CHARGE_POINTS_PAYLOAD)
    with pytest.raises(SystemExit) as exc:
        _match_charge_point(charge_points, "charger")
    assert exc.value.code == 2
    assert "be more specific" in capsys.readouterr().err


def test_resolve_unknown_selector_exits_2(capsys):
    charge_points = _charge_points(CHARGE_POINTS_PAYLOAD)
    with pytest.raises(SystemExit) as exc:
        _match_charge_point(charge_points, "nonexistent")
    assert exc.value.code == 2
    assert "No charge point matches" in capsys.readouterr().err


# --- Command behaviour ----------------------------------------------------


async def test_status_shows_power_and_active_session(cli, capsys):
    with respx.mock:
        respx.get(USER_URL).mock(return_value=httpx.Response(200, json=USER_PAYLOAD))
        respx.get(CP_URL).mock(
            return_value=httpx.Response(200, json=CHARGE_POINTS_PAYLOAD)
        )
        respx.get(DETAIL_URL).mock(
            return_value=httpx.Response(200, json=DETAIL_V3_PAYLOAD)
        )
        respx.get(SESSIONS_URL).mock(
            return_value=httpx.Response(200, json=SESSIONS_PAYLOAD)
        )
        await run(["status"])

    out = capsys.readouterr().out
    assert "Garage Charger (SN0000001)" in out
    assert "Network: ONLINE" in out
    assert "Connector 1: CHARGING" in out
    assert "Charging power: 3.60 kW" in out
    assert "Grid power: 0.40 kW" in out
    assert "Active session: 3.50 kWh" in out


async def test_status_json_is_the_only_thing_on_stdout(cli, capsys):
    with respx.mock:
        respx.get(USER_URL).mock(return_value=httpx.Response(200, json=USER_PAYLOAD))
        respx.get(CP_URL).mock(
            return_value=httpx.Response(200, json=CHARGE_POINTS_PAYLOAD)
        )
        respx.get(DETAIL_URL).mock(
            return_value=httpx.Response(200, json=DETAIL_V3_PAYLOAD)
        )
        respx.get(SESSIONS_URL).mock(
            return_value=httpx.Response(200, json=SESSIONS_PAYLOAD)
        )
        await run(["status", "--json"])

    captured = capsys.readouterr()
    # The whole of stdout must parse as a single JSON document
    payload = json.loads(captured.out)
    assert payload[0]["chargePoint"]["serial"] == "SN0000001"
    assert payload[0]["sessions"][0]["id"] == "session-0000001"


async def test_charge_points_list(cli, capsys):
    with respx.mock:
        respx.get(USER_URL).mock(return_value=httpx.Response(200, json=USER_PAYLOAD))
        respx.get(CP_URL).mock(
            return_value=httpx.Response(200, json=CHARGE_POINTS_PAYLOAD)
        )
        await run(["charge-points", "list"])

    out = capsys.readouterr().out
    assert "cp-0000001" in out
    assert "Garage Charger" in out
    assert "SN0000001" in out
    assert "ONLINE" in out


async def test_charge_points_show(cli, capsys):
    with respx.mock:
        respx.get(USER_URL).mock(return_value=httpx.Response(200, json=USER_PAYLOAD))
        respx.get(CP_URL).mock(
            return_value=httpx.Response(200, json=CHARGE_POINTS_PAYLOAD)
        )
        respx.get(DETAIL_URL).mock(
            return_value=httpx.Response(200, json=DETAIL_V3_PAYLOAD)
        )
        await run(["charge-points", "show"])

    out = capsys.readouterr().out
    assert "Model: E2" in out
    assert "Firmware: 1.2.3" in out
    assert "Charge schedule: enabled" in out


async def test_sessions_list(cli, capsys):
    with respx.mock:
        respx.get(USER_URL).mock(return_value=httpx.Response(200, json=USER_PAYLOAD))
        respx.get(CP_URL).mock(
            return_value=httpx.Response(200, json=CHARGE_POINTS_PAYLOAD)
        )
        respx.get(SESSIONS_URL).mock(
            return_value=httpx.Response(200, json=SESSIONS_PAYLOAD)
        )
        await run(["sessions", "list"])

    out = capsys.readouterr().out
    assert "active" in out  # the in-progress session has no end date
    assert "7.00 kWh" in out
    assert "1.96 NZD" in out


async def test_sessions_list_respects_limit(cli, capsys):
    with respx.mock:
        respx.get(USER_URL).mock(return_value=httpx.Response(200, json=USER_PAYLOAD))
        respx.get(CP_URL).mock(
            return_value=httpx.Response(200, json=CHARGE_POINTS_PAYLOAD)
        )
        respx.get(SESSIONS_URL).mock(
            return_value=httpx.Response(200, json=SESSIONS_PAYLOAD)
        )
        await run(["sessions", "list", "--limit", "1"])

    out = capsys.readouterr().out
    # Header plus exactly one data row
    assert len(out.strip().splitlines()) == 2
    assert "1.96 NZD" not in out


async def test_insights(cli, capsys):
    with respx.mock:
        respx.get(USER_URL).mock(return_value=httpx.Response(200, json=USER_PAYLOAD))
        respx.get(INSIGHTS_URL, params={"days": 7, "tz-offset": 12}).mock(
            return_value=httpx.Response(200, json=INSIGHTS_PAYLOAD)
        )
        await run(["insights"])

    out = capsys.readouterr().out
    assert "2024-06-10" in out
    assert "1.00 kWh" in out
    assert "1.50 NZD" in out


async def test_charge_now_sends_override(cli, capsys):
    with respx.mock:
        respx.get(USER_URL).mock(return_value=httpx.Response(200, json=USER_PAYLOAD))
        respx.get(CP_URL).mock(
            return_value=httpx.Response(200, json=CHARGE_POINTS_PAYLOAD)
        )
        route = respx.post(OVERRIDE_URL).mock(return_value=httpx.Response(200, json={}))
        await run(["charge", "now"])

    out = capsys.readouterr().out
    assert "Charging now on Garage Charger" in out
    assert json.loads(route.calls[0].request.content)["chargeNow"] is True


async def test_charge_auto_sends_override(cli, capsys):
    with respx.mock:
        respx.get(USER_URL).mock(return_value=httpx.Response(200, json=USER_PAYLOAD))
        respx.get(CP_URL).mock(
            return_value=httpx.Response(200, json=CHARGE_POINTS_PAYLOAD)
        )
        route = respx.post(OVERRIDE_URL).mock(return_value=httpx.Response(200, json={}))
        await run(["charge", "auto"])

    out = capsys.readouterr().out
    assert "charging schedule" in out
    assert json.loads(route.calls[0].request.content)["chargeNow"] is False


async def test_charge_stop(cli, capsys):
    with respx.mock:
        respx.get(USER_URL).mock(return_value=httpx.Response(200, json=USER_PAYLOAD))
        respx.get(CP_URL).mock(
            return_value=httpx.Response(200, json=CHARGE_POINTS_PAYLOAD)
        )
        respx.post(STOP_URL).mock(
            return_value=httpx.Response(
                200,
                json={"data": {"message": "Command accepted", "status": "Accepted"}},
            )
        )
        await run(["charge", "stop", "--yes"])

    assert "Stopped charging on Garage Charger" in capsys.readouterr().out


async def test_charge_stop_no_active_session_exits_1(cli, capsys):
    with respx.mock:
        respx.get(USER_URL).mock(return_value=httpx.Response(200, json=USER_PAYLOAD))
        respx.get(CP_URL).mock(
            return_value=httpx.Response(200, json=CHARGE_POINTS_PAYLOAD)
        )
        respx.post(STOP_URL).mock(side_effect=httpx.ReadTimeout("timeout"))
        with pytest.raises(SystemExit) as exc:
            await run(["charge", "stop", "--yes"])

    assert exc.value.code == 1
    assert "No active charging session" in capsys.readouterr().err


async def test_schedule_show(cli, capsys):
    with respx.mock:
        respx.get(USER_URL).mock(return_value=httpx.Response(200, json=USER_PAYLOAD))
        respx.get(CP_URL).mock(
            return_value=httpx.Response(200, json=CHARGE_POINTS_PAYLOAD)
        )
        respx.get(DETAIL_URL).mock(
            return_value=httpx.Response(200, json=DETAIL_V3_PAYLOAD)
        )
        await run(["schedule", "show"])

    out = capsys.readouterr().out
    assert "Charge schedule for Garage Charger: enabled" in out
    assert "00:00" in out
    assert "22:00" in out
    assert "32 A" in out


async def test_schedule_show_json(cli, capsys):
    with respx.mock:
        respx.get(USER_URL).mock(return_value=httpx.Response(200, json=USER_PAYLOAD))
        respx.get(CP_URL).mock(
            return_value=httpx.Response(200, json=CHARGE_POINTS_PAYLOAD)
        )
        respx.get(DETAIL_URL).mock(
            return_value=httpx.Response(200, json=DETAIL_V3_PAYLOAD)
        )
        await run(["schedule", "show", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["enabled"] is True
    assert payload["chargingSchedulePeriods"][0]["limit"] == 32


async def test_charge_stop_declined_prompt_aborts(cli, capsys, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    with respx.mock:
        respx.get(USER_URL).mock(return_value=httpx.Response(200, json=USER_PAYLOAD))
        respx.get(CP_URL).mock(
            return_value=httpx.Response(200, json=CHARGE_POINTS_PAYLOAD)
        )
        stop = respx.post(STOP_URL)
        with pytest.raises(SystemExit) as exc:
            await run(["charge", "stop"])

    assert exc.value.code == 1
    assert "Aborted" in capsys.readouterr().err
    assert stop.call_count == 0  # the command was never sent


async def test_charge_stop_accepted_prompt_sends_command(cli, capsys, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    with respx.mock:
        respx.get(USER_URL).mock(return_value=httpx.Response(200, json=USER_PAYLOAD))
        respx.get(CP_URL).mock(
            return_value=httpx.Response(200, json=CHARGE_POINTS_PAYLOAD)
        )
        stop = respx.post(STOP_URL).mock(
            return_value=httpx.Response(
                200,
                json={"data": {"message": "Command accepted", "status": "Accepted"}},
            )
        )
        await run(["charge", "stop"])

    assert stop.call_count == 1


@pytest.mark.parametrize(
    "argv",
    [
        ["charge-points", "list", "--json"],
        ["sessions", "list", "--json"],
        ["insights", "--json"],
    ],
)
async def test_json_purity_on_listings(cli, capsys, argv):
    with respx.mock:
        respx.get(USER_URL).mock(return_value=httpx.Response(200, json=USER_PAYLOAD))
        respx.get(CP_URL).mock(
            return_value=httpx.Response(200, json=CHARGE_POINTS_PAYLOAD)
        )
        respx.get(SESSIONS_URL).mock(
            return_value=httpx.Response(200, json=SESSIONS_PAYLOAD)
        )
        respx.get(INSIGHTS_URL).mock(
            return_value=httpx.Response(200, json=INSIGHTS_PAYLOAD)
        )
        await run(argv)

    out = capsys.readouterr().out
    json.loads(out)  # stdout is exactly one JSON document


async def test_sessions_ordering_is_enforced(cli, capsys):
    # Serve the sessions oldest-first; the CLI must still show newest first
    reversed_payload = {"data": list(reversed(SESSIONS_PAYLOAD["data"]))}
    with respx.mock:
        respx.get(USER_URL).mock(return_value=httpx.Response(200, json=USER_PAYLOAD))
        respx.get(CP_URL).mock(
            return_value=httpx.Response(200, json=CHARGE_POINTS_PAYLOAD)
        )
        respx.get(SESSIONS_URL).mock(
            return_value=httpx.Response(200, json=reversed_payload)
        )
        await run(["sessions", "list", "--limit", "1"])

    out = capsys.readouterr().out
    assert "active" in out  # the newest (active) session, not the oldest


async def test_status_renders_charge_point_without_meter(cli, capsys):
    detail = json.loads(json.dumps(DETAIL_V3_PAYLOAD))
    detail["data"]["attributes"]["connectors"][0]["meter"] = None
    with respx.mock:
        respx.get(USER_URL).mock(return_value=httpx.Response(200, json=USER_PAYLOAD))
        respx.get(CP_URL).mock(
            return_value=httpx.Response(200, json=CHARGE_POINTS_PAYLOAD)
        )
        respx.get(DETAIL_URL).mock(return_value=httpx.Response(200, json=detail))
        respx.get(SESSIONS_URL).mock(
            return_value=httpx.Response(200, json=SESSIONS_PAYLOAD)
        )
        await run(["status"])

    out = capsys.readouterr().out
    assert "Garage Charger" in out  # renders, no crash, no meter lines


def test_sessions_limit_must_be_positive():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["sessions", "list", "--limit", "-1"])
