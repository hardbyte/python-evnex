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

