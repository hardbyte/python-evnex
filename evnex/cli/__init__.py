"""Command line interface for the EVNEX Cloud API client.

Run without installing anything via uv:

    uvx evnex auth login
    uvx evnex status
    uvx evnex charge-points list
    uvx evnex sessions list
    uvx evnex charge now

Credentials come from EVNEX_CLIENT_USERNAME / EVNEX_CLIENT_PASSWORD (or are
prompted for). Session tokens are cached with 0600 permissions so an MFA
sign-in is only needed occasionally, not per command.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from importlib.metadata import version
from pathlib import Path

import httpx
from pydantic import ValidationError

from evnex.cli._auth import (
    _challenge_code,
    _default_cache,
    _load_tokens,
    add_auth_commands,
    cmd_change_password,
    cmd_login,
    cmd_logout,
    cmd_mfa_confirm,
    cmd_mfa_disable,
    cmd_mfa_enable,
    cmd_mfa_enroll,
    cmd_reset_password,
    cmd_status,
    signed_in_auth,
)
from evnex.cli._resources import add_resource_commands
from evnex.errors import EvnexAuthError

__all__ = [
    "_challenge_code",
    "_load_tokens",
    "build_parser",
    "cmd_change_password",
    "cmd_login",
    "cmd_logout",
    "cmd_mfa_confirm",
    "cmd_mfa_disable",
    "cmd_mfa_enable",
    "cmd_mfa_enroll",
    "cmd_reset_password",
    "cmd_status",
    "main",
    "signed_in_auth",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evnex",
        description="Command line interface for the EVNEX Cloud API.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"evnex {version('evnex')}",
    )
    parser.set_defaults(func=None, print_group_help=parser.print_help)

    # Flags for commands that read or write the token cache.
    cache_flags = argparse.ArgumentParser(add_help=False)
    cache_flags.add_argument(
        "--token-cache",
        type=Path,
        default=_default_cache(),
        help=f"where to cache session tokens (default: {_default_cache()})",
    )
    # Flags for commands that may need to answer a sign-in MFA challenge. Kept
    # separate so commands that never sign in (logout) or need no session at
    # all (reset-password) reject them instead of silently ignoring them.
    otp_flags = argparse.ArgumentParser(add_help=False)
    otp_flags.add_argument(
        "--otp",
        help="6-digit code to answer a sign-in MFA challenge non-interactively",
    )
    otp_flags.add_argument(
        "--otp-command",
        help="shell command printing a current MFA code, e.g. "
        "'op item get Evnex --otp' with the 1Password CLI",
    )

    sub = parser.add_subparsers(dest="command")
    add_auth_commands(sub, cache_flags, otp_flags)
    add_resource_commands(sub, cache_flags, otp_flags)

    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    handler = getattr(args, "func", None)
    if handler is None:
        # No (leaf) subcommand: print the most specific help and exit cleanly.
        args.print_group_help()
        sys.exit(0)
    try:
        asyncio.run(handler(args))
    except EvnexAuthError as err:
        print(f"Authentication error: {err}", file=sys.stderr)
        sys.exit(1)
    except httpx.HTTPError as err:
        print(f"API request failed: {err}", file=sys.stderr)
        sys.exit(1)
    except ValidationError:
        print(
            "The API returned a response this client version does not"
            " understand; try upgrading evnex",
            file=sys.stderr,
        )
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
