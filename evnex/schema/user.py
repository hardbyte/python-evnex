from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from evnex.schema.org import EvnexOrgBrief


class EvnexUserDetail(BaseModel):
    id: UUID
    createdDate: datetime
    updatedDate: datetime
    # The API omits name entirely for accounts that never set one
    name: str | None = None
    email: str
    organisations: list[EvnexOrgBrief]
    type: Literal["User", "Installer"] = "User"


class EvnexGetUserResponse(BaseModel):
    data: EvnexUserDetail
