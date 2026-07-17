"""Command line interface for the EVNEX Cloud API client.

Run without installing anything via uv:

    uvx evnex auth login
    uvx evnex auth status
    uvx evnex auth mfa enable
    uvx evnex auth change-password
    uvx evnex auth reset-password

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
from importlib.metadata import version
from pathlib import Path

import jwt

from evnex.auth import AuthChallenge, EvnexAuth, TokenSet
from evnex.errors import EvnexAuthError, ReauthenticationRequiredError

DEFAULT_CACHE = (
    Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    / "evnex"
    / "tokens.json"
)


def _default_cache() -> Path:
    override = os.environ.get("EVNEX_TOKEN_CACHE")
    return Path(override) if override else DEFAULT_CACHE


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
    if args.otp:
        code, args.otp = args.otp, None  # a code is single-use
        return str(code)
    if args.otp_command:
        proc = await asyncio.create_subprocess_shell(
            args.otp_command, stdout=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        print("Code obtained from --otp-command", file=sys.stderr)
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


def _enrollment_account() -> str:
    return os.environ.get("EVNEX_CLIENT_USERNAME", "evnex-account")


async def cmd_login(args: argparse.Namespace) -> None:
    await signed_in_auth(args)


async def cmd_logout(args: argparse.Namespace) -> None:
    cache: Path = args.token_cache
    if cache.is_file():
        cache.unlink()
        print(f"Removed cached session at {cache}")
    else:
        print("No cached session")


async def cmd_status(args: argparse.Namespace) -> None:
    auth = await signed_in_auth(args)
    tokens = auth.tokens
    identity = "unknown"
    if tokens is not None and tokens.id_token:
        try:
            claims = jwt.decode(tokens.id_token, options={"verify_signature": False})
            identity = claims.get("email") or claims.get("cognito:username") or identity
        except jwt.DecodeError:
            pass
    print(f"Signed in as: {identity}")
    if tokens is not None and tokens.expires_at is not None:
        print(f"Access token expires: {tokens.expires_at.isoformat()}")
    print(f"Token cache: {args.token_cache}")

    status = await auth.get_mfa_status()
    if not status.enabled:
        print("MFA: disabled")
        return
    print("MFA methods:")
    for method in status.enabled:
        marker = " (preferred)" if method == status.preferred else ""
        print(f"  {method}{marker}")


async def cmd_change_password(args: argparse.Namespace) -> None:
    auth = await signed_in_auth(args)
    current = getpass.getpass("Current password: ")
    new = getpass.getpass("New password: ")
    confirm = getpass.getpass("Confirm new password: ")
    if new != confirm:
        print("New passwords did not match; aborted", file=sys.stderr)
        return
    await auth.change_password(current, new)
    print("Password changed")


async def cmd_reset_password(args: argparse.Namespace) -> None:
    # The forgot-password flow needs no signed-in session.
    auth = EvnexAuth()
    username = os.environ.get("EVNEX_CLIENT_USERNAME") or input("EVNEX username: ")
    destination = await auth.start_password_reset(username)
    if destination:
        print(f"A reset code was sent to {destination}")
    else:
        print("A reset code was sent; check your email")
    code = input("Enter the reset code: ")
    new = getpass.getpass("New password: ")
    confirm = getpass.getpass("Confirm new password: ")
    if new != confirm:
        print("New passwords did not match; aborted", file=sys.stderr)
        return
    await auth.confirm_password_reset(username, code, new)
    print("Password reset; sign in again with the new password")


async def cmd_mfa_enable(args: argparse.Namespace) -> None:
    auth = await signed_in_auth(args)
    enrollment = await auth.begin_totp_enrollment()
    uri = enrollment.provisioning_uri(_enrollment_account())

    print("Scan the QR code with your authenticator app, or paste the")
    print("otpauth URI into a password manager's one-time password field:\n")
    print(f"  {uri}\n")
    print(f"(bare secret for manual entry: {enrollment.secret})\n")
    show_qr(uri, open_browser=args.browser)

    code = input("Enter a code from the new device: ")
    await auth.confirm_totp_enrollment(code, args.device_name)
    await auth.set_mfa_preference(totp=True)
    print("TOTP device registered and set as the preferred MFA method")


async def cmd_mfa_disable(args: argparse.Namespace) -> None:
    if not args.yes:
        answer = input("Disable MFA on this account? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("Aborted")
            return
    auth = await signed_in_auth(args)
    await auth.set_mfa_preference()
    print("MFA disabled")


async def cmd_mfa_enroll(args: argparse.Namespace) -> None:
    auth = await signed_in_auth(args)
    enrollment = await auth.begin_totp_enrollment()
    uri = enrollment.provisioning_uri(_enrollment_account())

    print("Scan the QR code with your authenticator app, or paste the")
    print("otpauth URI into a password manager's one-time password field:\n")
    print(f"  {uri}\n")
    print(f"(bare secret for manual entry: {enrollment.secret})\n")
    show_qr(uri, open_browser=args.browser)
    print("\nThen run: evnex auth mfa confirm CODE [--device-name NAME]")


async def cmd_mfa_confirm(args: argparse.Namespace) -> None:
    auth = await signed_in_auth(args)
    await auth.confirm_totp_enrollment(args.totp_code, args.device_name)
    if args.prefer:
        await auth.set_mfa_preference(totp=True)
        print("TOTP device registered and set as the preferred MFA method")
    else:
        print("TOTP device registered (MFA preference unchanged)")


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
    parser.set_defaults(handler=None, print_group_help=parser.print_help)

    # Shared flags accepted in trailing position on every auth leaf command.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--token-cache",
        type=Path,
        default=_default_cache(),
        help=f"where to cache session tokens (default: {_default_cache()})",
    )
    common.add_argument(
        "--otp",
        help="6-digit code to answer a sign-in MFA challenge non-interactively",
    )
    common.add_argument(
        "--otp-command",
        help="shell command printing a current MFA code, e.g. "
        "'op item get Evnex --otp' with the 1Password CLI",
    )

    sub = parser.add_subparsers(dest="command")

    auth = sub.add_parser(
        "auth",
        help="manage authentication and MFA for your EVNEX account",
        description=(
            "Sign in and manage MFA and passwords for your EVNEX account. "
            "Credentials come from EVNEX_CLIENT_USERNAME / EVNEX_CLIENT_PASSWORD "
            "or prompts; session tokens are cached so an MFA code is only "
            "needed once."
        ),
    )
    auth.set_defaults(print_group_help=auth.print_help)
    auth_sub = auth.add_subparsers(dest="auth_command")

    login = auth_sub.add_parser(
        "login",
        parents=[common],
        help="sign in (using cached tokens when valid) and cache session tokens",
    )
    login.set_defaults(handler="login")

    logout = auth_sub.add_parser(
        "logout",
        parents=[common],
        help="delete the cached session tokens",
    )
    logout.set_defaults(handler="logout")

    status = auth_sub.add_parser(
        "status",
        parents=[common],
        help="show the signed-in user, session, and enabled MFA methods",
    )
    status.set_defaults(handler="status")

    change_password = auth_sub.add_parser(
        "change-password",
        parents=[common],
        help="change the account password (prompts for current and new)",
    )
    change_password.set_defaults(handler="change-password")

    reset_password = auth_sub.add_parser(
        "reset-password",
        parents=[common],
        help="reset a forgotten password via an emailed code (no sign-in)",
    )
    reset_password.set_defaults(handler="reset-password")

    mfa = auth_sub.add_parser(
        "mfa",
        help="manage multi-factor authentication devices",
        description="Enable, disable, or (re)enroll TOTP multi-factor authentication.",
    )
    mfa.set_defaults(print_group_help=mfa.print_help)
    mfa_sub = mfa.add_subparsers(dest="mfa_command")

    enable = mfa_sub.add_parser(
        "enable",
        parents=[common],
        help="enroll a new TOTP device and make it the preferred MFA method",
        description=(
            "Interactive one-shot: prints the otpauth:// URI, bare secret, and "
            "a QR code, prompts for a code from the new device, then registers "
            "it and makes TOTP the preferred MFA method. Enrolling a new device "
            "replaces any previously registered one."
        ),
    )
    enable.add_argument("--device-name", default="", help="friendly device name")
    enable.add_argument(
        "--browser", action="store_true", help="also open the QR code in a browser"
    )
    enable.set_defaults(handler="mfa-enable")

    disable = mfa_sub.add_parser(
        "disable",
        parents=[common],
        help="turn MFA off for the account",
        description="Disable all MFA methods for the account.",
    )
    disable.add_argument(
        "--yes", "-y", action="store_true", help="skip the confirmation prompt"
    )
    disable.set_defaults(handler="mfa-disable")

    enroll = mfa_sub.add_parser(
        "enroll",
        parents=[common],
        help="print a TOTP enrollment URI/secret/QR and exit (for automation)",
        description=(
            "Plumbing command: start enrolling a TOTP device and print the "
            "otpauth:// URI, bare secret, and QR code, then exit. Complete "
            "enrollment with 'evnex auth mfa confirm CODE'."
        ),
    )
    enroll.add_argument(
        "--browser", action="store_true", help="also open the QR code in a browser"
    )
    enroll.set_defaults(handler="mfa-enroll")

    confirm = mfa_sub.add_parser(
        "confirm",
        parents=[common],
        help="verify a code from a newly enrolled TOTP device (for automation)",
        description=(
            "Plumbing command: verify a code generated by the newly enrolled "
            "device. By default this also makes TOTP the preferred MFA method."
        ),
    )
    confirm.add_argument("totp_code", help="6-digit code from the new device")
    confirm.add_argument("--device-name", default="", help="friendly device name")
    confirm.add_argument(
        "--no-prefer",
        dest="prefer",
        action="store_false",
        help="register the device without changing the MFA preference",
    )
    confirm.set_defaults(handler="mfa-confirm")

    return parser


HANDLERS = {
    "login": cmd_login,
    "logout": cmd_logout,
    "status": cmd_status,
    "change-password": cmd_change_password,
    "reset-password": cmd_reset_password,
    "mfa-enable": cmd_mfa_enable,
    "mfa-disable": cmd_mfa_disable,
    "mfa-enroll": cmd_mfa_enroll,
    "mfa-confirm": cmd_mfa_confirm,
}


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        # No (leaf) subcommand: print the most specific help and exit cleanly.
        args.print_group_help()
        sys.exit(0)
    try:
        asyncio.run(HANDLERS[handler](args))
    except EvnexAuthError as err:
        print(f"Authentication error: {err}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
