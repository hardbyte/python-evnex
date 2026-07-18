"""Authentication, MFA, and password commands, plus shared sign-in helpers.

The interactive input()/getpass() prompts throughout this module block the
event loop on purpose: this is a single-task CLI process, so there is no
concurrent work for them to hold up.
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

import jwt

from evnex.auth import AuthChallenge, EvnexAuth, TokenSet, TotpEnrollment
from evnex.errors import ReauthenticationRequiredError

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
        # EvnexAuth awaits this callback under its lock, so keep the blocking
        # filesystem work off the event loop.
        def _write() -> None:
            cache.parent.mkdir(parents=True, exist_ok=True)
            # os.open + fchmod pins the mode to 0600 even when the cache file
            # already exists; touch(mode=…) leaves a pre-existing file's
            # permissions untouched.
            fd = os.open(cache, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.fchmod(fd, 0o600)
                os.write(fd, json.dumps(tokens.to_dict()).encode())
            finally:
                os.close(fd)

        await asyncio.to_thread(_write)

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
            args.otp_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        code = stdout.decode().strip()
        if proc.returncode != 0:
            if stderr.strip():
                print(stderr.decode().rstrip(), file=sys.stderr)
            print(f"--otp-command failed (exit {proc.returncode})", file=sys.stderr)
            sys.exit(1)
        if not code:
            print("--otp-command produced no code", file=sys.stderr)
            sys.exit(1)
        print("Code obtained from --otp-command", file=sys.stderr)
        return code
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
            "(install the qrcode package for a scannable QR code)",
            file=sys.stderr,
        )
        return

    qr = qrcode.QRCode(border=2)
    qr.add_data(uri)
    qr.print_ascii(tty=sys.stdout.isatty())
    if open_browser:
        image = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage)
        # Prefer $XDG_RUNTIME_DIR (tmpfs, cleared at logout) for a file that
        # holds a live MFA secret; fall back to the default temp dir. The
        # browser reads it after we return, so it cannot be auto-deleted.
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or None
        with tempfile.NamedTemporaryFile(
            suffix=".svg", dir=runtime_dir, delete=False, mode="wb"
        ) as f:
            image.save(f)
        os.chmod(f.name, 0o600)
        webbrowser.open(f"file://{f.name}")
        print(
            f"QR code written to {f.name}\n"
            "This file contains your MFA secret; delete it after scanning.",
            file=sys.stderr,
        )


def _enrollment_account() -> str:
    return os.environ.get("EVNEX_CLIENT_USERNAME", "evnex-account")


def _print_enrollment(enrollment: TotpEnrollment, *, open_browser: bool) -> None:
    """Print the otpauth URI, bare secret, and QR code for a TOTP enrollment."""
    uri = enrollment.provisioning_uri(_enrollment_account())
    print("Scan the QR code with your authenticator app, or paste the")
    print("otpauth URI into a password manager's one-time password field:\n")
    print(f"  {uri}\n")
    print(f"(bare secret for manual entry: {enrollment.secret})\n")
    show_qr(uri, open_browser=open_browser)


async def cmd_login(args: argparse.Namespace) -> None:
    await signed_in_auth(args)


async def cmd_logout(args: argparse.Namespace) -> None:
    cache: Path = args.token_cache

    def _remove() -> bool:
        if cache.is_file():
            cache.unlink()
            return True
        return False

    if await asyncio.to_thread(_remove):
        print(f"Removed cached session at {cache}")
    else:
        print("No cached session")


async def cmd_status(args: argparse.Namespace) -> None:
    auth = await signed_in_auth(args)
    tokens = auth.tokens
    if tokens is None or not tokens.id_token:
        identity = "unknown (no identity token cached)"
    else:
        try:
            claims = jwt.decode(tokens.id_token, options={"verify_signature": False})
        except jwt.InvalidTokenError:
            claims = {}
        identity = claims.get("email") or claims.get("cognito:username") or "unknown"
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
        print("New passwords did not match. Aborted.", file=sys.stderr)
        sys.exit(1)
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
        print("New passwords did not match. Aborted.", file=sys.stderr)
        sys.exit(1)
    await auth.confirm_password_reset(username, code, new)
    print("Password reset; sign in again with the new password")


async def cmd_mfa_enable(args: argparse.Namespace) -> None:
    auth = await signed_in_auth(args)
    enrollment = await auth.begin_totp_enrollment()
    _print_enrollment(enrollment, open_browser=args.browser)

    code = input("Enter a code from the new device: ")
    await auth.confirm_totp_enrollment(code, args.device_name)
    await auth.set_mfa_preference(totp=True)
    print("TOTP device registered and set as the preferred MFA method")


async def cmd_mfa_disable(args: argparse.Namespace) -> None:
    if not args.yes:
        answer = input("Disable MFA on this account? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("Aborted.", file=sys.stderr)
            sys.exit(1)
    auth = await signed_in_auth(args)
    await auth.set_mfa_preference()
    print("MFA disabled")


async def cmd_mfa_enroll(args: argparse.Namespace) -> None:
    auth = await signed_in_auth(args)
    enrollment = await auth.begin_totp_enrollment()
    _print_enrollment(enrollment, open_browser=args.browser)
    print("\nThen run: evnex auth mfa confirm CODE [--device-name NAME]")


async def cmd_mfa_confirm(args: argparse.Namespace) -> None:
    auth = await signed_in_auth(args)
    await auth.confirm_totp_enrollment(args.totp_code, args.device_name)
    if args.prefer:
        await auth.set_mfa_preference(totp=True)
        print("TOTP device registered and set as the preferred MFA method")
    else:
        print("TOTP device registered (MFA preference unchanged)")


def add_auth_commands(
    sub: argparse._SubParsersAction,
    cache_flags: argparse.ArgumentParser,
    otp_flags: argparse.ArgumentParser,
) -> None:
    """Attach the `auth` command group to the top-level subparsers."""
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
        parents=[cache_flags, otp_flags],
        help="sign in (using cached tokens when valid) and cache session tokens",
    )
    login.set_defaults(func=cmd_login)

    logout = auth_sub.add_parser(
        "logout",
        parents=[cache_flags],
        help="delete the cached session tokens",
    )
    logout.set_defaults(func=cmd_logout)

    status = auth_sub.add_parser(
        "status",
        parents=[cache_flags, otp_flags],
        help="show the signed-in user, session, and enabled MFA methods",
    )
    status.set_defaults(func=cmd_status)

    change_password = auth_sub.add_parser(
        "change-password",
        parents=[cache_flags, otp_flags],
        help="change the account password (prompts for current and new)",
    )
    change_password.set_defaults(func=cmd_change_password)

    reset_password = auth_sub.add_parser(
        "reset-password",
        help="reset a forgotten password via an emailed code (no sign-in)",
    )
    reset_password.set_defaults(func=cmd_reset_password)

    mfa = auth_sub.add_parser(
        "mfa",
        help="manage multi-factor authentication devices",
        description="Enable, disable, or (re)enroll TOTP multi-factor authentication.",
    )
    mfa.set_defaults(print_group_help=mfa.print_help)
    mfa_sub = mfa.add_subparsers(dest="mfa_command")

    enable = mfa_sub.add_parser(
        "enable",
        parents=[cache_flags, otp_flags],
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
    enable.set_defaults(func=cmd_mfa_enable)

    disable = mfa_sub.add_parser(
        "disable",
        parents=[cache_flags, otp_flags],
        help="turn MFA off for the account",
        description="Disable all MFA methods for the account.",
    )
    disable.add_argument(
        "--yes", "-y", action="store_true", help="skip the confirmation prompt"
    )
    disable.set_defaults(func=cmd_mfa_disable)

    enroll = mfa_sub.add_parser(
        "enroll",
        parents=[cache_flags, otp_flags],
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
    enroll.set_defaults(func=cmd_mfa_enroll)

    confirm = mfa_sub.add_parser(
        "confirm",
        parents=[cache_flags, otp_flags],
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
    confirm.set_defaults(func=cmd_mfa_confirm)
