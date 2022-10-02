from pydantic import BaseModel


class EvnexElectricityTariff(BaseModel):
    start: float
    rate: float
    type: str   # Flat


class EvnexElectricityCost(BaseModel):
    currency: str # NZD
    tariffs: list[EvnexElectricityTariff]
    tariffType: str
    cost: float
