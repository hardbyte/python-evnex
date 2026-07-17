from pydantic_settings import BaseSettings


class EvnexConfig(BaseSettings):
    EVNEX_BASE_URL: str = "https://client-api.evnex.io"
    EVNEX_COGNITO_USER_POOL_ID: str = "ap-southeast-2_zWnqo6ASv"
    EVNEX_COGNITO_CLIENT_ID: str = "rol3lsv2vg41783550i18r7vi"
    EVNEX_ORG_ID: str | None = None
