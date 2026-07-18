from datetime import datetime
from typing import Any

from pydantic import BaseModel

from evnex.schema.cost import EvnexCost


class EvnexOrgBrief(BaseModel):
    id: str
    isDefault: bool
    role: int
    createdDate: datetime
    name: str
    slug: str
    tier: int
    tierDetails: Any = None
    updatedDate: datetime
    namespacePrefix: str | None = None


class EvnexOrgInsightEntry(BaseModel):
    carbonOffset: float
    carbonUsage: float | None = None
    cost: EvnexCost
    duration: int
    # Watt-hours, like all energy figures in this API (the official app
    # and integrations render these via Wh sensors)
    powerUsage: float
    sessions: int
    startDate: datetime


class EvnexInsightAttributeWrapper(BaseModel):
    attributes: EvnexOrgInsightEntry


class EvnexOrgSummaryStatus(BaseModel):
    charging: int
    available: int
    disabled: int
    faulted: int
    occupied: int
    offline: int
    reserved: int


class EvnexGetOrgInsights(BaseModel):
    data: list[EvnexInsightAttributeWrapper]


class EvnexGetOrgSummaryStatusResponse(BaseModel):
    data: EvnexOrgSummaryStatus
