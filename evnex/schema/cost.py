from pydantic import BaseModel


class EvnexCost(BaseModel):
    currency: str | None = None
    cost: float | None = None
