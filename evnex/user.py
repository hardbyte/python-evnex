from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import httpx
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


def get_user_detail(token) -> EvnexUserDetail:
    r = httpx.get(
        'https://client-api.evnex.io/v2/apps/user',
        headers={
            'Accept': 'application/json',
            'Authorization': token
        }
    )
    r.raise_for_status()
    data = EvnexGetUserResponse(**r.json()).data
    return data
