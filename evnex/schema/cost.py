from pydantic import BaseModel


class EvnexCost(BaseModel):
    currency: str = None
    cost: float = None
