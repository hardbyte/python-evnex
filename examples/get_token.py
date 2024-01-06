try:
    from pydantic.v1 import BaseSettings, SecretStr
except ImportError:
    from pydantic import BaseSettings, SecretStr

from evnex.api import Evnex


class EvnexAuthDetails(BaseSettings):
    EVNEX_CLIENT_USERNAME: str
    EVNEX_CLIENT_PASSWORD: SecretStr


if __name__ == "__main__":
    creds = EvnexAuthDetails()
    evnex = Evnex(
        username=creds.EVNEX_CLIENT_USERNAME,
        password=creds.EVNEX_CLIENT_PASSWORD.get_secret_value(),
    )

    print(evnex.access_token)
