from typing import Any

from pydantic import BaseModel


class EvnexElectricityTariff(BaseModel):
    start: float
    rate: float
    type: str  # Flat


class EvnexElectricityCost(BaseModel):
    currency: str  # NZD
    tariffs: list[EvnexElectricityTariff]
    tariffType: str
    cost: float | None = None


class EvnexElectricityCostTotal(BaseModel):
    currency: str  # NZD
    amount: float
    distribution: Any = None
