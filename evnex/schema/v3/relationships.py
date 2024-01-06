from typing import Optional

try:
    from pydantic.v1 import BaseModel
except ImportError:
    from pydantic import BaseModel


class EvnexRelationship(BaseModel):
    id: str
    type: str


class EvnexRelationshipWrapper(BaseModel):
    data: Optional[EvnexRelationship]


class EvnexRelationships(BaseModel):
    chargePoint: Optional[EvnexRelationshipWrapper]
    location: Optional[EvnexRelationshipWrapper]
    organisation: Optional[EvnexRelationshipWrapper]
