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

Authentication lives in an `EvnexAuth` object; the `Evnex` client makes API
calls with it. Credentials are used once to establish a session and never
stored:

```python
import asyncio
from pydantic import SecretStr
from pydantic_settings import BaseSettings
from evnex.api import Evnex
from evnex.auth import EvnexAuth


class EvnexAuthDetails(BaseSettings):
    EVNEX_CLIENT_USERNAME: str
    EVNEX_CLIENT_PASSWORD: SecretStr


async def main():
    creds = EvnexAuthDetails()
    auth = EvnexAuth()
    await auth.start_authentication(
        creds.EVNEX_CLIENT_USERNAME,
        creds.EVNEX_CLIENT_PASSWORD.get_secret_value(),
    )
    evnex = Evnex(auth=auth)

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

When the account has MFA enabled, `start_authentication` returns an
`AuthChallenge` instead of tokens. Obtain a code from the user and answer it:

```python
from evnex.auth import AuthChallenge

result = await auth.start_authentication(username, password)
while isinstance(result, AuthChallenge):
    code = input(f"Enter the 6-digit code ({result.name}): ")
    result = await auth.respond_to_challenge(result, code)
```

The Cognito challenge session is short-lived (around 3 minutes).
`ChallengeExpiredError` means start over with `start_authentication`;
`InvalidChallengeResponseError` means the code was wrong and the same
challenge can be retried. An `AuthChallenge` is JSON-serializable
(`to_dict()`/`from_dict()`), so it can be answered by a different process
within the session lifetime.

### Resuming a session and persisting tokens

Resume with stored tokens — the refresh token alone is enough, no password
needed. Register `on_token_update` to persist every newly issued token set;
it is awaited before any request proceeds with the new tokens, so storage
stays consistent even if the process dies mid-refresh:

```python
from evnex.auth import EvnexAuth, TokenSet

async def save_tokens(tokens: TokenSet) -> None:
    my_store.write(tokens.to_dict())

auth = EvnexAuth(
    tokens=TokenSet.from_dict(my_store.read()),
    on_token_update=save_tokens,
)
evnex = Evnex(auth=auth)
user_data = await evnex.get_user_detail()  # no password or MFA prompt
```

When the API rejects an expired or revoked access token, the transport layer
refreshes the session and resends the request once, transparently. If the
refresh token itself is no longer valid, calls raise
`ReauthenticationRequiredError` — run the interactive flow again.

See `examples/get_token.py` for a complete MFA + token persistence flow.

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

