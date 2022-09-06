from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class EvnexOrgBrief(BaseModel):
    id: UUID
    isDefault: bool
    role: int
    createdDate: datetime
    name: str
    slug: str
    tier: int
    tierDetails: Any
    updatedDate: datetime
