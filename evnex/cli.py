"""Command line interface for the EVNEX Cloud API client.

Run without installing anything via uv:

    uvx evnex auth status
    uvx evnex auth enroll-totp
    uvx evnex auth confirm-totp CODE --device-name NAME
    uvx evnex auth disable

Credentials come from EVNEX_CLIENT_USERNAME / EVNEX_CLIENT_PASSWORD (or are
prompted for). Session tokens are cached with 0600 permissions so an MFA
sign-in is only needed occasionally, not per command.
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


async def _challenge_code(args: argparse.Namespace, challenge: AuthChallenge) -> str:
    if args.code:
        code, args.code = args.code, None  # a code is single-use
        return str(code)
    if args.code_command:
        proc = await asyncio.create_subprocess_shell(
            args.code_command, stdout=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        print("Code obtained from --code-command", file=sys.stderr)
        return stdout.decode().strip()
    return input(f"Enter the 6-digit code ({challenge.name}): ")


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
        result = await auth.respond_to_challenge(
            result, await _challenge_code(args, result)
        )
    print(f"Signed in as {username}; session cached at {cache}", file=sys.stderr)
    return auth


def show_qr(uri: str, open_browser: bool) -> None:
    """Render the enrollment QR in the terminal, and optionally a browser."""
    try:
        import qrcode
        import qrcode.image.svg
    except ImportError:
        print(
            "(for a scannable QR code, run with the qrcode package:"
            " uvx --with qrcode evnex ...)",
            file=sys.stderr,
        )
        return

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

    print("Scan the QR code with your authenticator app, or paste the")
    print("otpauth URI into a password manager's one-time password field:\n")
    print(f"  {uri}\n")
    print(f"(bare secret for manual entry: {enrollment.secret})\n")
    show_qr(uri, open_browser=args.browser)
    print("\nThen run: evnex auth confirm-totp CODE [--device-name NAME]")


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evnex",
        description="Command line interface for the EVNEX Cloud API.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    auth = sub.add_parser(
        "auth",
        help="manage authentication and MFA for your EVNEX account",
        description=(
            "Sign in and manage MFA for your EVNEX account. Credentials come "
            "from EVNEX_CLIENT_USERNAME / EVNEX_CLIENT_PASSWORD or prompts; "
            "session tokens are cached so an MFA code is only needed once."
        ),
    )
    auth.add_argument(
        "--token-cache",
        type=Path,
        default=DEFAULT_CACHE,
        help=f"where to cache session tokens (default: {DEFAULT_CACHE})",
    )
    auth.add_argument(
        "--code",
        help="6-digit code to answer a sign-in MFA challenge non-interactively",
    )
    auth.add_argument(
        "--code-command",
        help="shell command printing a current MFA code, e.g. "
        "'op item get Evnex --otp' with the 1Password CLI",
    )
    auth_sub = auth.add_subparsers(dest="auth_command", required=True)

    auth_sub.add_parser("status", help="show which MFA methods are enabled")
    auth_sub.add_parser("signin", help="sign in and cache session tokens")

    enroll = auth_sub.add_parser(
        "enroll-totp",
        help="enroll a new TOTP authenticator device",
        description=(
            "Start enrolling a TOTP device: prints the otpauth:// URI (paste "
            "into a password manager's one-time password field) and a QR code. "
            "Completing enrollment with confirm-totp replaces any previously "
            "registered device."
        ),
    )
    enroll.add_argument(
        "--browser", action="store_true", help="also open the QR code in a browser"
    )

    confirm = auth_sub.add_parser(
        "confirm-totp",
        help="verify the new TOTP device and enable it",
        description=(
            "Verify a code generated by the newly enrolled device. By default "
            "this also makes TOTP the preferred MFA method."
        ),
    )
    confirm.add_argument("totp_code", help="6-digit code from the new device")
    confirm.add_argument("--device-name", default="", help="friendly device name")
    confirm.add_argument(
        "--no-enable",
        dest="enable",
        action="store_false",
        help="register the device without changing the MFA preference",
    )

    disable = auth_sub.add_parser(
        "disable",
        help="turn MFA off for the account",
        description="Disable all MFA methods for the account.",
    )
    disable.add_argument("--yes", action="store_true", help="skip confirmation")

    return parser


HANDLERS = {
    "status": cmd_status,
    "signin": cmd_signin,
    "enroll-totp": cmd_enroll_totp,
    "confirm-totp": cmd_confirm_totp,
    "disable": cmd_disable,
}


def main() -> None:
    args = build_parser().parse_args()
    try:
        asyncio.run(HANDLERS[args.auth_command](args))
    except EvnexAuthError as err:
        print(f"Authentication error: {err}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
