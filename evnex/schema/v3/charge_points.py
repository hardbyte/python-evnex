from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from evnex.schema.v3.cost import EvnexElectricityCost


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
