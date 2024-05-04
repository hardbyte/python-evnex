from pydantic import BaseModel


class EvnexCost(BaseModel):
    currency: str
    cost: float
