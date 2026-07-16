# python-evnex

[![CI](https://github.com/hardbyte/python-evnex/actions/workflows/ci.yml/badge.svg)](https://github.com/hardbyte/python-evnex/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/evnex.svg)](https://pypi.org/project/evnex/)

Python client for the Evnex API.

Author not affiliated with Evnex.

## Features 

- Talks to your Evnex charger via Cloud API
- Automatic retries with exponential backoff
- Automatic re-authentication
- Optionally pass in a `httpx` client
- Optionally pass in tokens to resume an existing session

## Installation

```
pip install evnex
```

**Requirements:** Python 3.11+


## Usage

```python
import asyncio
from pydantic import SecretStr
from pydantic_settings import BaseSettings
from evnex.api import Evnex


class EvnexAuthDetails(BaseSettings):
    EVNEX_CLIENT_USERNAME: str
    EVNEX_CLIENT_PASSWORD: SecretStr


async def main():
    creds = EvnexAuthDetails()
    evnex = Evnex(username=creds.EVNEX_CLIENT_USERNAME,
                  password=creds.EVNEX_CLIENT_PASSWORD.get_secret_value())

    user_data = await evnex.get_user_detail()

    for org in user_data.organisations:
        print("Getting 7 day insight for", org.name, "User:", user_data.name)
        insights = await evnex.get_org_insight(days=7, org_id=org.id)

        for segment in insights:
            print(segment)


if __name__ == '__main__':
    asyncio.run(main())
```

### Multifactor authentication

Accounts with MFA enabled can't authenticate with username and password alone:
the first API call raises a challenge exception. Catch it, obtain a code from
the user, then respond to the challenge:

```python
from pycognito.exceptions import (
    SMSMFAChallengeException,
    SoftwareTokenMFAChallengeException,
)

evnex = Evnex(username=username, password=password)
try:
    evnex.authenticate()
except SoftwareTokenMFAChallengeException:
    code = input("Enter the 6-digit code from your authenticator application: ")
    evnex.respond_to_mfa_challenge(code, "TOTP")
except SMSMFAChallengeException:
    code = input("Enter the 6-digit code you received by SMS: ")
    evnex.respond_to_mfa_challenge(code, "SMS")
```

Note the Cognito challenge session is short-lived (around 3 minutes), so
prompt for the code promptly.

The pending challenge is stored on the `Evnex` instance, so the example above
works because the same instance calls `authenticate()` and
`respond_to_mfa_challenge()`. If the response happens somewhere else — a
different process, or a web backend handling a later request — capture the
challenge session from the exception and hand it to the new instance:

```python
try:
    evnex.authenticate()
except SoftwareTokenMFAChallengeException as challenge:
    challenge_session = challenge.get_tokens()  # JSON-serializable dict

# later, on a fresh Evnex instance
evnex.respond_to_mfa_challenge(code, "TOTP", mfa_tokens=challenge_session)
```

### Resuming a session

To avoid interactive MFA on every start, store the tokens after a successful
authentication and pass them back in later — the refresh token alone is
enough. When the API rejects an expired or revoked access token, the client
refreshes it and retries the request automatically:

```python
evnex = Evnex(username=username, password=password, refresh_token=refresh_token)
user_data = await evnex.get_user_detail()  # no MFA prompt needed
```

See `examples/get_token.py` for a complete MFA + token resumption flow.

## Examples

`python-evnex` is intended as a library, but a few example scripts are provided in the `examples` folder.

Providing authentication for the examples is via environment variables, e.g. on nix systems:

```
export EVNEX_CLIENT_USERNAME=you@example.com
export EVNEX_CLIENT_PASSWORD=<your password>

python -m examples.get_charge_point_detail
```

## Developer Notes

### Development Setup

```shell
# Install dependencies with development tools
uv sync --group dev

# Set up pre-commit hooks (recommended)
uv run pre-commit install

# Alternatively, format and lint manually
uv run ruff format .
uv run ruff check .
```

### Making a new release

What ends up on PyPi is what really matters. Creating a release in GitHub triggers a release workflow that builds and publishes to PyPi.

To manually release, update the version in `pyproject.toml`, build and publish with uv:

```shell
uv build
uv publish
```

