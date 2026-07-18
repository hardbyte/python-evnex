from datetime import datetime

from pydantic import BaseModel, Field


class EvnexLocationAddress(BaseModel):
    address1: str | None = None
    address2: str | None = None
    city: str | None = None
    postCode: str | None = None
    state: str | None = None
    country: str | None = None


class EvnexLocationCoordinates(BaseModel):
    latitude: str | None = None
    longitude: str | None = None


class EvnexLocationIcpDetails(BaseModel):
    electricityRetailer: str | None = None
    electricityDistributor: str | None = None
    networkConnectionPoint: str | None = None


class EvnexLocationAttributes(BaseModel):
    name: str
    address: EvnexLocationAddress | None = None
    coordinates: EvnexLocationCoordinates | None = None
    isPublic: bool | None = None
    updated: datetime | None = None
    created: datetime | None = None
    icpNumber: str | None = None
    icpDetails: EvnexLocationIcpDetails | None = None
    timeZone: str | None = None


class EvnexLocationChargePointRef(BaseModel):
    type: str
    id: str


class EvnexLocationChargePoints(BaseModel):
    data: list[EvnexLocationChargePointRef] = Field(default_factory=list)


class EvnexLocationRelationships(BaseModel):
    chargePoints: EvnexLocationChargePoints = Field(
        default_factory=EvnexLocationChargePoints
    )


class EvnexLocation(BaseModel):
    id: str
    type: str
    attributes: EvnexLocationAttributes
    relationships: EvnexLocationRelationships = Field(
        default_factory=EvnexLocationRelationships
    )


class EvnexGetLocationsResponse(BaseModel):
    data: list[EvnexLocation]
