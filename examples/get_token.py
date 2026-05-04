"""
Example script to get a token from AWS cognito. Supports MFA authentication.

"""

from pycognito.exceptions import (
    SMSMFAChallengeException,
    SoftwareTokenMFAChallengeException,
)
from pydantic import SecretStr
from pydantic_settings import BaseSettings

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

    try:
        evnex.authenticate()

    except SMSMFAChallengeException:
        code = input("Enter the 6-digit code you received by SMS: ")
        evnex.respond_to_mfa_challenge(code, "SMS")

    except SoftwareTokenMFAChallengeException:
        code = input("Enter the 6-digit code from your authenticator application: ")
        evnex.respond_to_mfa_challenge(code, "TOTP")

    print("Access Token: ", evnex.access_token)
