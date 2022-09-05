from functools import lru_cache

from pycognito import Cognito
from pydantic import BaseSettings, HttpUrl


class EvnexConfig(BaseSettings):
    EVNEX_BASE_URL: HttpUrl = "https://client-api.evnex.io"
    COGNITO_USER_POOL_ID: str = "ap-southeast-2_zWnqo6ASv"
    COGNITO_CLIENT_ID: str = "rol3lsv2vg41783550i18r7vi"


def _authenticate(username, password, config) -> Cognito:
    if config is None:
        config = EvnexConfig()
    u = Cognito(config.COGNITO_USER_POOL_ID, config.COGNITO_CLIENT_ID, username=username)
    # If this method call succeeds the instance will have the following attributes:
    # id_token, refresh_token, access_token, expires_in, expires_datetime, and token_type.
    try:
        u.authenticate(password=password)
    except:
        raise ValueError("Problem with authentication")
    return u


@lru_cache
def retrieve_auth_token(username: str, password: str, config: EvnexConfig = None):
    u = _authenticate(username, password, config)
    return u.access_token


