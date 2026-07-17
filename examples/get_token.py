#!/usr/bin/env python3
"""
Example: sign in (with MFA if enabled) and print the session tokens.
"""

import asyncio
import logging

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from evnex.api import Evnex
from evnex.auth import AuthChallenge, EvnexAuth, TokenSet


class EvnexAuthDetails(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVNEX_")

    CLIENT_USERNAME: str
    CLIENT_PASSWORD: SecretStr
    ACCESS_TOKEN: str | None = None
    REFRESH_TOKEN: str | None = None


async def save_tokens(tokens: TokenSet) -> None:
    # A real application would persist these atomically
    print("New tokens issued; persist them for next time")


async def main():
    creds = EvnexAuthDetails()

    tokens = None
    if creds.ACCESS_TOKEN or creds.REFRESH_TOKEN:
        tokens = TokenSet(
            access_token=creds.ACCESS_TOKEN or "",
            refresh_token=creds.REFRESH_TOKEN,
        )

    auth = EvnexAuth(tokens=tokens, on_token_update=save_tokens)

    if tokens is None:
        result = await auth.start_authentication(
            creds.CLIENT_USERNAME, creds.CLIENT_PASSWORD.get_secret_value()
        )
        while isinstance(result, AuthChallenge):
            code = input(f"Enter the code for challenge {result.name}: ")
            result = await auth.respond_to_challenge(result, code)

    evnex = Evnex(auth=auth)
    user = await evnex.get_user_detail()
    print("User Name:", user.name or user.email)

    print("Access Token: ", auth.tokens.access_token)
    print("Refresh Token: ", auth.tokens.refresh_token)
    print("Expires At: ", auth.tokens.expires_at)


if __name__ == "__main__":
    # Note: DEBUG level logs request headers including bearer tokens
    logging.basicConfig(level=logging.INFO)

    asyncio.run(main())
