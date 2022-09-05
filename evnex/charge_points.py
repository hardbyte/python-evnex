from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, Field


class EvnexChargePointConnectorMeter(BaseModel):
    powerType: str          # "AC_1_PHASE"
    updatedDate: datetime
    power: float
    _register: float = Field(alias='register')
    frequency: float


class Coordinates(BaseModel):
    latitude: float
    longitude: float


class EvnexAddress(BaseModel):
    address1: str
    address2: str | None
    city: str | None
    postCode: str | None
    state: str | None
    country: str


class EvnexLocation(BaseModel):
    id: UUID
    name: str
    createdDate: datetime
    updatedDate: datetime
    address: EvnexAddress | None
    coordinates: Coordinates | None
    chargePointCount: int


class EvnexChargePointConnector(BaseModel):
    powerType: str
    connectorId: str
    evseId: str
    updatedDate: str
    connectorType: str
    amperage: float
    voltage: float
    connectorFormat: str
    ocppStatus: str
    status: str
    ocppCode: str
    meter: EvnexChargePointConnectorMeter



class EvnexChargePointDetails(BaseModel):
    model: str
    vendor: str
    firmware: str
    iccid: str


class EvnexChargePointBase(BaseModel):
    # Attributes shared by brief and detail endpoints
    id: UUID
    createdDate: datetime
    updatedDate: datetime
    networkStatusUpdatedDate: datetime
    name: str
    ocppChargePointId: str
    serial: str
    networkStatus: str  # Could probably be an enum
    location: EvnexLocation


class EvnexChargePoint(EvnexChargePointBase):

    details: EvnexChargePointDetails
    connectors: list[EvnexChargePointConnector]
    lastHeard: datetime
    maxCurrent: float
    tokenRequired: bool
    needsRegistrationInformation: bool


class EvnexGetChargePointsItem(BaseModel):
    items: list[EvnexChargePoint]


class EvnexGetChargePointsResponse(BaseModel):
    data: EvnexGetChargePointsItem


class EvnexElectricityCostSegment(BaseModel):
    cost: float
    start: float


class EvnexChargeProfileSegment(BaseModel):
    limit: float
    start: float


class EvnexElectricityCost(BaseModel):
    currency: str
    duration: int
    costs: list[EvnexElectricityCostSegment]


class EvnexChargePointConfiguration(BaseModel):
    maxCurrent: float
    plugAndCharge: bool


class EvnexChargePointLoadSchedule(BaseModel):
    duration: int
    enabled: bool
    timezone: str
    units: str
    chargingProfilePeriods: list[EvnexChargeProfileSegment]


class EvnexChargePointDetail(EvnexChargePointBase):
    configuration: EvnexChargePointConfiguration
    electricityCost: EvnexElectricityCost
    loadSchedule: EvnexChargePointLoadSchedule


class EvnexGetChargePointDetailResponse(BaseModel):
    data: EvnexChargePointDetail


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

