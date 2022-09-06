import httpx

from evnex.schema.user import EvnexGetUserResponse, EvnexUserDetail


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
