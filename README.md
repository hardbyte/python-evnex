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

`EvnexAuth` handles signing in and keeping the session alive; the `Evnex`
client uses it to call the API. Credentials establish a session once and are
never stored:

```python
import asyncio
import os

from evnex.api import Evnex
from evnex.auth import EvnexAuth


async def main():
    auth = EvnexAuth()
    await auth.start_authentication(
        os.environ["EVNEX_CLIENT_USERNAME"],
        os.environ["EVNEX_CLIENT_PASSWORD"],
    )
    evnex = Evnex(auth=auth)

    user = await evnex.get_user_detail()
    for org in user.organisations:
        for entry in await evnex.get_org_insight(days=7, org_id=org.id):
            print(org.name, entry)


asyncio.run(main())
```

### Multi-factor authentication

If the account has MFA enabled, `start_authentication` returns an
`AuthChallenge` instead of tokens. Show it to the user, collect their
6-digit code, and answer it:

```python
from evnex.auth import AuthChallenge

result = await auth.start_authentication(username, password)
while isinstance(result, AuthChallenge):
    code = input(f"Enter the 6-digit code ({result.name}): ")
    result = await auth.respond_to_challenge(result, code)
```

Challenges are short-lived (a few minutes): `ChallengeExpiredError` means
start over, while `InvalidChallengeResponseError` means the code was wrong
and the same challenge can be retried. A challenge serializes to JSON
(`to_dict()`/`from_dict()`), so a web backend or config flow can answer it
in a later request or another process.

### Staying signed in

Store the tokens and resume later — the refresh token alone is enough, no
password or MFA prompt. Expired sessions renew automatically; register
`on_token_update` to be handed every newly issued token set, and it will
have completed before any request uses the new tokens, so your stored copy
can never fall behind one that's already in use:

```python
from evnex.auth import EvnexAuth, TokenSet

async def save_tokens(tokens: TokenSet) -> None:
    my_store.write(tokens.to_dict())

auth = EvnexAuth(
    tokens=TokenSet.from_dict(my_store.read()),
    on_token_update=save_tokens,
)
evnex = Evnex(auth=auth)
user = await evnex.get_user_detail()
```

If a request is rejected mid-session, the client refreshes and retries it
once, transparently. When the session truly can't be renewed, calls raise
`ReauthenticationRequiredError` — run the interactive sign-in again.

See `examples/get_token.py` for a complete sign-in and persistence flow.

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

