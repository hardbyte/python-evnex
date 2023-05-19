from datetime import datetime
from typing import Optional, Any

from pydantic import BaseModel, Field

from evnex.schema.v3.cost import EvnexElectricityCost, EvnexElectricityCostTotal
from evnex.schema.v3.relationships import EvnexRelationships


class EvnexEnergyTransaction(BaseModel):
    meterStart: float
    startDate: datetime
    meterStop: float | None
    endDate: datetime | None
    reason: str | None


class EvnexEnergyUsage(BaseModel):
    total: float
    distributionByTariff: Any
    distributionByEnergySource: Any


class EvnexChargeSchedulePeriod(BaseModel):
    limit: float
    startPeriod: float


class EvnexChargeSchedule(BaseModel):
    enabled: bool
    chargingSchedulePeriods: list[EvnexChargeSchedulePeriod]


class EvnexChargeProfile(BaseModel):
    chargeSchedule: Optional[EvnexChargeSchedule]


class EvnexChargePointConnectorMeter(BaseModel):
    currentL1: Optional[float]
    frequency: float
    power: float
    _register: float = Field(alias="register")
    updatedDate: datetime
    voltageL1N: Optional[float]


class EvnexChargePointConnector(BaseModel):
    evseId: str
    connectorFormat: str  # CABLE
    connectorType: str
    ocppStatus: str
    powerType: str  # AC_1_PHASE
    connectorId: str
    ocppCode: str  # CHARGING
    updatedDate: datetime
    meter: EvnexChargePointConnectorMeter
    maxVoltage: float
    maxAmperage: float


class EvnexChargePointDetail(BaseModel):
    connectors: list[EvnexChargePointConnector]
    createdDate: datetime
    electricityCost: EvnexElectricityCost
    firmware: str
    iccid: str
    maxCurrent: float
    model: str
    name: str
    networkStatus: str
    networkStatusUpdatedDate: datetime
    ocppChargePointId: str
    profiles: EvnexChargeProfile
    serial: str
    timeZone: str
    tokenRequired: bool
    updatedDate: datetime
    vendor: str


class EvnexChargePointSession(BaseModel):
    totalCarbonUsage: float | None
    chargingStarted: datetime | None
    chargingStopped: datetime | None
    connectorId: str | None
    createdDate: datetime | None
    evseId: str | None
    sessionStatus: str | None
    startDate: datetime | None
    updatedDate: datetime | None
    authorizationMethod: str | None
    electricityCost: EvnexElectricityCost | None
    endDate: datetime | None
    totalChargingTime: float | None
    totalDuration: float | None
    totalEnergyUsage: EvnexEnergyUsage | None
    totalCost: EvnexElectricityCostTotal | None
    totalPowerUsage: float | None
    transaction: EvnexEnergyTransaction | None


class EvnexChargePointSessions(BaseModel):
    attributes: EvnexChargePointSession
    id: str
    type: str
    relationships: EvnexRelationships | None


class EvnexGetChargePointSessionsResponse(BaseModel):
    data: list[EvnexChargePointSessions]
