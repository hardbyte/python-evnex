from pydantic import BaseModel


class EvnexRelationship(BaseModel):
    id: str
    type: str


class EvnexRelationshipWrapper(BaseModel):
    data: EvnexRelationship | None = None


class EvnexRelationships(BaseModel):
    chargePoint: EvnexRelationshipWrapper | None = None
    location: EvnexRelationshipWrapper | None = None
    organisation: EvnexRelationshipWrapper | None = None
