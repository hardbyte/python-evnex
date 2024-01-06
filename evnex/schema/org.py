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
    tierDetails: Any
    updatedDate: datetime


class EvnexOrgInsightEntry(BaseModel):
    carbonOffset: float
    costs: list[EvnexCost]
    duration: int
    powerUsage: float
    startDate: datetime
    sessions: int


class EvnexGetOrgInsights(BaseModel):
    items: list[EvnexOrgInsightEntry]


class EvnexGetOrgInsightResponse(BaseModel):
    data: EvnexGetOrgInsights
