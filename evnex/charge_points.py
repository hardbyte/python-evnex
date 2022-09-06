import httpx

from evnex.schema.charge_points import EvnexChargePoint, EvnexChargePointDetail, EvnexGetChargePointDetailResponse
from evnex.schema.charge_points import EvnexGetChargePointsResponse


def get_org_charge_points(token, org_id) -> list[EvnexChargePoint]:
    r = httpx.get(
        f'https://client-api.evnex.io/v2/apps/organisations/{org_id}/charge-points',
        headers={
            'Accept': 'application/json',
            'Authorization': token
        }
    )
    r.raise_for_status()
    return EvnexGetChargePointsResponse(**r.json()).data.items


def get_charge_point_detail(token: str, charge_point_id: str) -> EvnexChargePointDetail:
    r = httpx.get(
        f'https://client-api.evnex.io/v2/apps/charge-points/{charge_point_id}',
        headers={
            'Accept': 'application/json',
            'Authorization': token
        }
    )
    r.raise_for_status()
    print(r.json())
    return EvnexGetChargePointDetailResponse(**r.json()).data

