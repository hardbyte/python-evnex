"""Schema regression tests built from captured API payloads."""

from evnex.schema.user import EvnexGetUserResponse
from evnex.schema.v3.charge_points import EvnexChargePointConnectorMeter


def test_user_without_name_validates():
    # The API omits the name field entirely for accounts that never set one
    payload = {
        "data": {
            "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
            "createdDate": "2022-01-01T00:00:00Z",
            "updatedDate": "2022-01-01T00:00:00Z",
            "email": "user@example.com",
            "organisations": [],
            "type": "User",
        }
    }
    user = EvnexGetUserResponse.model_validate(payload).data
    assert user.name is None
    assert user.email == "user@example.com"


def test_connector_meter_exposes_supply_active_power():
    # Captured from a live v3 charge point detail response for a charger
    # with the PowerSensor feature (CT clamp) installed
    payload = {
        "currentL1": 24.2,
        "frequency": 50,
        "power": 5380,
        "register": 7502488,
        "supplyActivePower": 7730,
        "updatedDate": "2026-07-16T10:25:00.000Z",
        "voltageL1N": 222.3,
    }
    meter = EvnexChargePointConnectorMeter.model_validate(payload)
    assert meter.supplyActivePower == 7730


def test_connector_meter_without_power_sensor():
    payload = {
        "frequency": 50,
        "power": 5380,
        "register": 7502488,
        "updatedDate": "2026-07-16T10:25:00.000Z",
    }
    meter = EvnexChargePointConnectorMeter.model_validate(payload)
    assert meter.supplyActivePower is None
