#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "evnex",
#     "qrcode>=8.0",
# ]
#
# [tool.uv.sources]
# evnex = { path = "..", editable = true }
# ///
"""Manage MFA on your EVNEX account: view status, enroll or replace a TOTP
device, or disable MFA — none of which the EVNEX app currently exposes.

Usage (credentials via EVNEX_CLIENT_USERNAME / EVNEX_CLIENT_PASSWORD):

    uv run examples/manage_mfa.py status
    uv run examples/manage_mfa.py enroll-totp [--browser]
    uv run examples/manage_mfa.py confirm-totp CODE [--device-name NAME]
    uv run examples/manage_mfa.py disable --yes

Session tokens are cached (0600) so MFA sign-in is only needed once; pass
--code to answer a sign-in challenge non-interactively.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
import tempfile
import webbrowser
from pathlib import Path

import qrcode
import qrcode.image.svg

from evnex.auth import AuthChallenge, EvnexAuth, TokenSet
from evnex.errors import EvnexAuthError, ReauthenticationRequiredError

DEFAULT_CACHE = (
    Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    / "evnex"
    / "tokens.json"
)


def _save_tokens_factory(cache: Path):
    async def save_tokens(tokens: TokenSet) -> None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.touch(mode=0o600, exist_ok=True)
        cache.write_text(json.dumps(tokens.to_dict()))

    return save_tokens


def _load_tokens(cache: Path) -> TokenSet | None:
    if cache.is_file():
        try:
            return TokenSet.from_dict(json.loads(cache.read_text()))
        except (ValueError, KeyError):
            print(f"Ignoring unreadable token cache at {cache}", file=sys.stderr)
    return None


async def signed_in_auth(args: argparse.Namespace) -> EvnexAuth:
    """Return an EvnexAuth with a usable session, signing in if needed."""
    cache: Path = args.token_cache
    auth = EvnexAuth(
        tokens=_load_tokens(cache), on_token_update=_save_tokens_factory(cache)
    )
    if auth.tokens is not None:
        try:
            await auth.get_access_token()
            return auth
        except ReauthenticationRequiredError:
            print("Cached session expired; signing in again", file=sys.stderr)

    username = os.environ.get("EVNEX_CLIENT_USERNAME") or input("EVNEX username: ")
    password = os.environ.get("EVNEX_CLIENT_PASSWORD") or getpass.getpass(
        "EVNEX password: "
    )
    result = await auth.start_authentication(username, password)
    while isinstance(result, AuthChallenge):
        code = args.code or input(f"Enter the 6-digit code ({result.name}): ")
        args.code = None  # a code is single-use
        result = await auth.respond_to_challenge(result, code)
    print(f"Signed in as {username}; session cached at {cache}", file=sys.stderr)
    return auth


def show_qr(uri: str, open_browser: bool) -> None:
    qr = qrcode.QRCode(border=2)
    qr.add_data(uri)
    qr.print_ascii(tty=sys.stdout.isatty())
    if open_browser:
        image = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage)
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False, mode="wb") as f:
            image.save(f)
        webbrowser.open(f"file://{f.name}")
        print(f"QR code opened in browser ({f.name})", file=sys.stderr)


async def cmd_status(args: argparse.Namespace) -> None:
    auth = await signed_in_auth(args)
    status = await auth.get_mfa_status()
    if not status.enabled:
        print("MFA: disabled")
        return
    for method in status.enabled:
        marker = " (preferred)" if method == status.preferred else ""
        print(f"MFA enabled: {method}{marker}")


async def cmd_enroll_totp(args: argparse.Namespace) -> None:
    auth = await signed_in_auth(args)
    enrollment = await auth.begin_totp_enrollment()
    account = os.environ.get("EVNEX_CLIENT_USERNAME", "evnex-account")
    uri = enrollment.provisioning_uri(account)

    print("Scan this QR code with your authenticator app,")
    print(f"or add the secret manually: {enrollment.secret}\n")
    show_qr(uri, open_browser=args.browser)
    print(
        "\nThen run: uv run examples/manage_mfa.py confirm-totp CODE"
        " [--device-name NAME]"
    )


async def cmd_confirm_totp(args: argparse.Namespace) -> None:
    auth = await signed_in_auth(args)
    await auth.confirm_totp_enrollment(args.totp_code, args.device_name)
    if args.enable:
        await auth.set_mfa_preference(totp=True)
        print("TOTP device registered and set as the preferred MFA method")
    else:
        print("TOTP device registered (MFA preference unchanged)")


async def cmd_disable(args: argparse.Namespace) -> None:
    if not args.yes:
        answer = input("Disable MFA on this account? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("Aborted")
            return
    auth = await signed_in_auth(args)
    await auth.set_mfa_preference()
    print("MFA disabled")


async def cmd_signin(args: argparse.Namespace) -> None:
    await signed_in_auth(args)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--token-cache",
        type=Path,
        default=DEFAULT_CACHE,
        help=f"where to cache session tokens (default: {DEFAULT_CACHE})",
    )
    parser.add_argument("--code", help="6-digit code to answer a sign-in MFA challenge")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="show which MFA methods are enabled")
    sub.add_parser("signin", help="sign in and cache session tokens")

    enroll = sub.add_parser("enroll-totp", help="enroll a (new) TOTP device")
    enroll.add_argument(
        "--browser", action="store_true", help="also open the QR code in a browser"
    )

    confirm = sub.add_parser("confirm-totp", help="confirm the new TOTP device")
    confirm.add_argument("totp_code", help="6-digit code from the new device")
    confirm.add_argument("--device-name", default="", help="friendly device name")
    confirm.add_argument(
        "--no-enable",
        dest="enable",
        action="store_false",
        help="register the device without changing the MFA preference",
    )

    disable = sub.add_parser("disable", help="turn MFA off for the account")
    disable.add_argument("--yes", action="store_true", help="skip confirmation")

    args = parser.parse_args()
    handler = {
        "status": cmd_status,
        "signin": cmd_signin,
        "enroll-totp": cmd_enroll_totp,
        "confirm-totp": cmd_confirm_totp,
        "disable": cmd_disable,
    }[args.command]
    try:
        asyncio.run(handler(args))
    except EvnexAuthError as err:
        print(f"Authentication error: {err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
