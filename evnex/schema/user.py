from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from evnex.schema.org import EvnexOrgBrief


class EvnexUserDetail(BaseModel):
    id: UUID
    createdDate: datetime
    updatedDate: datetime
    name: str
    email: str
    organisations: list[EvnexOrgBrief]
    type: Literal['User'] = 'User'


class EvnexGetUserResponse(BaseModel):
    data: EvnexUserDetail
