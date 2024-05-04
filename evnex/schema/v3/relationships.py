from typing import Optional

from pydantic import BaseModel


class EvnexRelationship(BaseModel):
    id: str
    type: str


class EvnexRelationshipWrapper(BaseModel):
    data: Optional[EvnexRelationship] = None


class EvnexRelationships(BaseModel):
    chargePoint: Optional[EvnexRelationshipWrapper] = None
    location: Optional[EvnexRelationshipWrapper] = None
    organisation: Optional[EvnexRelationshipWrapper] = None
