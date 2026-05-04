from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from evnex.schema.v3.cost import EvnexElectricityCost, EvnexElectricityCostTotal
from evnex.schema.v3.relationships import EvnexRelationships


class EvnexEnergyTransaction(BaseModel):
    meterStart: float
    startDate: datetime
    meterStop: float | None = None
    endDate: datetime | None = None
    reason: str | None = None


class EvnexEnergyUsage(BaseModel):
    total: float
    distributionByTariff: Any = None
    distributionByEnergySource: Any = None


class EvnexChargeSchedulePeriod(BaseModel):
    limit: float
    startPeriod: float


class EvnexChargeSchedule(BaseModel):
    enabled: bool
    chargingSchedulePeriods: list[EvnexChargeSchedulePeriod]


class EvnexChargeProfile(BaseModel):
    chargeSchedule: EvnexChargeSchedule | None = None


class EvnexChargePointFeature(BaseModel):
    unlocked: bool


class EvnexChargePointFeatures(BaseModel):
    PowerSensor: EvnexChargePointFeature
    Solar: EvnexChargePointFeature
    VehicleIntegration: EvnexChargePointFeature


class EvnexChargePointConnectorMeter(BaseModel):
    currentL1: float | None = None
    currentL2: float | None = None
    currentL3: float | None = None
    frequency: float
    power: float
    raw_register: float = Field(..., alias="register")
    updatedDate: datetime
    temperature: float | None = None
    voltageL1N: float | None = None
    voltageL2N: float | None = None
    voltageL3N: float | None = None


class EvnexChargePointConnector(BaseModel):
    evseId: str
    connectorFormat: str  # CABLE
    connectorType: str
    ocppStatus: str
    powerType: str  # AC_1_PHASE
    connectorId: str
    ocppCode: str  # CHARGING
    updatedDate: datetime
    meter: EvnexChargePointConnectorMeter | None = None
    maxVoltage: float
    maxAmperage: float


class EvnexChargePointConnectionConfiguration(BaseModel):
    automaticallyManaged: bool
    preferredConnectionType: str  # Cell
    updatedDate: datetime
    wifiConnected: bool


class EvnexChargePointDetail(BaseModel):
    connectors: list[EvnexChargePointConnector]
    createdDate: datetime
    electricityCost: EvnexElectricityCost
    firmware: str
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
    connectionConfiguration: EvnexChargePointConnectionConfiguration | None = None
    features: EvnexChargePointFeatures | None = None
    iccid: str | None = None
    isSolarEnabled: bool | None = None


class EvnexChargePointSessionAttributes(BaseModel):
    totalCarbonUsage: float | None = None
    chargingStarted: datetime | None = None
    chargingStopped: datetime | None = None
    connectorId: str | None = None
    createdDate: datetime | None = None
    evseId: str | None = None
    sessionStatus: str | None = None
    startDate: datetime | None = None
    updatedDate: datetime | None = None
    authorizationMethod: str | None = None
    electricityCost: EvnexElectricityCost | None = None
    endDate: datetime | None = None
    totalChargingTime: float | None = None
    totalDuration: float | None = None
    totalEnergyUsage: EvnexEnergyUsage | None = None
    totalCost: EvnexElectricityCostTotal | None = None
    totalPowerUsage: float | None = None
    transaction: EvnexEnergyTransaction | None = None


class EvnexChargePointSession(BaseModel):
    attributes: EvnexChargePointSessionAttributes
    id: str
    type: str
    relationships: EvnexRelationships | None = None


class EvnexGetChargePointSessionsResponse(BaseModel):
    data: list[EvnexChargePointSession]
