from typing import Any

try:
    from pydantic.v1 import BaseModel
except ImportError:
    from pydantic import BaseModel


class EvnexElectricityTariff(BaseModel):
    start: float
    rate: float
    type: str  # Flat


class EvnexElectricityCost(BaseModel):
    currency: str  # NZD
    tariffs: list[EvnexElectricityTariff]
    tariffType: str
    cost: float | None


class EvnexElectricityCostTotal(BaseModel):
    currency: str  # NZD
    amount: float
    distribution: Any
