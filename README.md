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

### Managing MFA devices

The EVNEX app doesn't currently expose changing or removing your MFA device;
the API does, and `EvnexAuth` wraps it (requires a signed-in session):

```python
status = await auth.get_mfa_status()          # which methods are enabled

enrollment = await auth.begin_totp_enrollment()
print(enrollment.provisioning_uri("you@example.com"))  # render as QR code
await auth.confirm_totp_enrollment(code, device_name="New phone")
await auth.set_mfa_preference(totp=True)      # turn TOTP on / make preferred

await auth.set_mfa_preference()               # disable MFA entirely
```

Completing a new TOTP enrollment replaces the previously registered
authenticator device.

### Changing or resetting your password

`EvnexAuth` also manages the account password:

```python
# Change the password of a signed-in account:
await auth.change_password(current_password, new_password)

# Reset a forgotten password (no session needed):
destination = await auth.start_password_reset("you@example.com")  # masked email
await auth.confirm_password_reset("you@example.com", emailed_code, new_password)
```

The CLI equivalents are `evnex auth change-password` (prompts for the current
and new password) and `evnex auth reset-password` (sends a code to your email,
then prompts for the code and a new password).

## Command line

Everything above is also available as a CLI, runnable directly with
[uv](https://docs.astral.sh/uv/):

```shell
export EVNEX_CLIENT_USERNAME=you@example.com
export EVNEX_CLIENT_PASSWORD=<your password>

uvx evnex auth login                 # sign in (uses cached tokens when valid)
uvx evnex auth status                # signed-in user, session, and MFA state
uvx evnex auth logout                # forget the cached session
uvx evnex auth mfa enable            # enroll a TOTP device and turn MFA on
uvx evnex auth mfa disable           # turn MFA off entirely
uvx evnex auth change-password       # change your password (prompts)
uvx evnex auth reset-password        # reset a forgotten password via email

uvx evnex status                     # live view: connectors, power, sessions
uvx evnex charge-points list         # id, name, serial, network status
uvx evnex charge-points show         # detail for one charge point
uvx evnex sessions list              # recent charging sessions
uvx evnex locations list             # name, city, ICP number, retailer, timezone
uvx evnex insights                   # daily energy, cost, and session counts
uvx evnex charge now                 # start charging immediately
uvx evnex charge auto                # return to the configured schedule
uvx evnex charge stop                # stop the active charging session
uvx evnex schedule show              # the configured charging schedule
```

The resource commands pick the charge point automatically when the account has
only one; otherwise select it with `--charge-point ID`, where `ID` is a charge
point id or a part of its name or serial of its name or serial. Add `--json` to `status`,
the listings, and `schedule show` for a machine-readable document on stdout:

```shell
uvx evnex status --json
```

`evnex auth status` shows who you are signed in as (decoded from the cached
token), when the session expires, and which MFA methods are enabled.

`evnex auth mfa enable` is the interactive one-shot: it prints an `otpauth://`
URI (paste it straight into a password manager's one-time password field),
the bare secret, and a QR code, then asks for a code from the new device and
makes TOTP the preferred method. For automation, the same flow is split into
`evnex auth mfa enroll` (print the URI/secret/QR and exit) and
`evnex auth mfa confirm CODE` (verify and enable; `--no-prefer` registers the
device without changing the MFA preference). The QR code renders in the
terminal, or in the browser with `--browser`.

Session tokens are cached (mode 0600, `~/.cache/evnex/tokens.json` by default,
or `EVNEX_TOKEN_CACHE`) so an MFA sign-in is only needed occasionally. To
answer sign-in challenges from a password manager instead of typing codes —
for example with the [1Password CLI](https://developer.1password.com/docs/cli/)
v2+:

```shell
uvx evnex auth login --otp-command 'op item get Evnex --otp'
```

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

