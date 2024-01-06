try:
    from pydantic.v1 import BaseModel
except ImportError:
    from pydantic import BaseModel


class EvnexCost(BaseModel):
    currency: str
    cost: float
