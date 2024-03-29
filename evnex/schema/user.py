from datetime import datetime
from typing import Literal
from uuid import UUID

try:
    from pydantic.v1 import BaseModel
except ImportError:
    from pydantic import BaseModel

from evnex.schema.org import EvnexOrgBrief


class EvnexUserDetail(BaseModel):
    id: UUID
    createdDate: datetime
    updatedDate: datetime
    name: str
    email: str
    organisations: list[EvnexOrgBrief]
    type: Literal["User", "Installer"] = "User"


class EvnexGetUserResponse(BaseModel):
    data: EvnexUserDetail
