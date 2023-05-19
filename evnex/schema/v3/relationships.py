from typing import Optional

from pydantic import BaseModel


class EvnexRelationship(BaseModel):
    id: str
    type: str


class EvnexRelationshipWrapper(BaseModel):
    data: EvnexRelationship


class EvnexRelationships(BaseModel):
    chargePoint: Optional[EvnexRelationshipWrapper]
    location: Optional[EvnexRelationshipWrapper]
    organisation: Optional[EvnexRelationshipWrapper]
