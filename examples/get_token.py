"""
Example script to get a token from AWS cognito. Supports MFA authentication.

"""

import asyncio

from pycognito.exceptions import (
    SMSMFAChallengeException,
    SoftwareTokenMFAChallengeException,
)
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from evnex.api import Evnex


class EvnexAuthDetails(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVNEX_")

    CLIENT_USERNAME: str
    CLIENT_PASSWORD: SecretStr
    ID_TOKEN: str | None = None
    REFRESH_TOKEN: str | None = None
    ACCESS_TOKEN: str | None = None


async def main():
    creds = EvnexAuthDetails()
    evnex = Evnex(
        username=creds.CLIENT_USERNAME,
        password=creds.CLIENT_PASSWORD.get_secret_value(),
        id_token=creds.ID_TOKEN,
        refresh_token=creds.REFRESH_TOKEN,
        access_token=creds.ACCESS_TOKEN,
    )

    if not creds.ID_TOKEN:
        try:
            evnex.authenticate()

        except SMSMFAChallengeException:
            code = input("Enter the 6-digit code you received by SMS: ")
            evnex.respond_to_mfa_challenge(code, "SMS")

        except SoftwareTokenMFAChallengeException:
            code = input("Enter the 6-digit code from your authenticator application: ")
            evnex.respond_to_mfa_challenge(code, "TOTP")
    else:
        user_details = await evnex.get_user_detail()
        print("User Name:", user_details.name)

    print("Access Token: ", evnex.access_token)
    print("Refresh Token: ", evnex.refresh_token)
    print("ID Token: ", evnex.id_token)


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.DEBUG)

    asyncio.run(main())
