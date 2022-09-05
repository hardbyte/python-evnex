from pydantic import BaseSettings, SecretStr

from evnex.auth import retrieve_auth_token


class EvnexAuthDetails(BaseSettings):
    EVNEX_CLIENT_USERNAME: str
    EVNEX_CLIENT_PASSWORD: SecretStr


if __name__ == '__main__':
    creds = EvnexAuthDetails()
    token = retrieve_auth_token(username=creds.EVNEX_CLIENT_USERNAME,
                                password=creds.EVNEX_CLIENT_PASSWORD.get_secret_value())
    print(token)
