from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from evnex.schema.cost import EvnexCost


class EvnexChargePointConnectorMeter(BaseModel):
    powerType: str  # "AC_1_PHASE"
    updatedDate: datetime
    power: float
    raw_register: float = Field(..., alias="register")
    frequency: float


class Coordinates(BaseModel):
    latitude: float
    longitude: float


class EvnexAddress(BaseModel):
    address1: str
    address2: str | None = None
    city: str | None = None
    postCode: str | None = None
    state: str | None = None
    country: str


class EvnexLocation(BaseModel):
    id: str
    name: str
    createdDate: datetime
    updatedDate: datetime
    address: EvnexAddress | None = None
    coordinates: Coordinates | None = None
    chargePointCount: int


class EvnexChargePointConnector(BaseModel):
    powerType: str  # AC_1_PHASE
    connectorId: str
    evseId: str
    updatedDate: datetime
    connectorType: str
    amperage: float
    voltage: float
    connectorFormat: str
    ocppStatus: str
    status: str  # OCCUPIED, CHARGING
    ocppCode: str  # CHARGING
    meter: EvnexChargePointConnectorMeter


class EvnexChargePointDetails(BaseModel):
    model: str
    vendor: str
    firmware: str
    iccid: Optional[str] = None


class EvnexChargePointSolarConfig(BaseModel):
    solarWithSchedule: bool
    powerSensorInstalled: bool
    solarStartExportPower: float
    solarStopImportPower: float


class EvnexChargePointOverrideConfig(BaseModel):
    chargeNow: bool | Literal["NotSupported"]


class EvnexChargePointBase(BaseModel):
    # Attributes shared by brief and detail endpoints
    id: str
    createdDate: datetime
    updatedDate: datetime
    networkStatusUpdatedDate: datetime
    name: str
    ocppChargePointId: str
    serial: str
    networkStatus: str  # Could probably be an enum ONLINE
    location: EvnexLocation


class EvnexChargePoint(EvnexChargePointBase):
    details: EvnexChargePointDetails
    connectors: list[EvnexChargePointConnector] | None = None
    lastHeard: datetime | None = None
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
    limit: int
    start: int


class EvnexElectricityCost(BaseModel):
    currency: str
    duration: int | None = None
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
    connectors: list[EvnexChargePointConnector]


class EvnexGetChargePointDetailResponse(BaseModel):
    data: EvnexChargePointDetail


class EvnexChargePointTransaction(BaseModel):
    id: str
    connectorId: str
    endDate: datetime | None = None
    evseId: str
    powerUsage: float
    reason: str | None = None  # EVDisconnected, Other
    startDate: datetime
    carbonOffset: float | None = None
    electricityCost: EvnexCost | None = None


class EvnexChargePointTransactions(BaseModel):
    items: list[EvnexChargePointTransaction]


class EvnexGetChargePointTransactionsResponse(BaseModel):
    data: EvnexChargePointTransactions
