"""Tests for the CLI parser wiring and command behaviour."""

import argparse
import asyncio

import pytest

from evnex.auth import AuthChallenge
from evnex.cli import (
    _challenge_code,
    _load_tokens,
    build_parser,
    cmd_change_password,
    cmd_login,
    cmd_logout,
    cmd_mfa_confirm,
    cmd_mfa_disable,
    cmd_mfa_enable,
    cmd_mfa_enroll,
    cmd_reset_password,
    cmd_status,
    main,
)


@pytest.mark.parametrize(
    "func, argv",
    [
        (cmd_login, ["auth", "login"]),
        (cmd_login, ["auth", "login", "--otp", "123456"]),
        (cmd_logout, ["auth", "logout"]),
        (cmd_status, ["auth", "status"]),
        (cmd_change_password, ["auth", "change-password"]),
        (cmd_reset_password, ["auth", "reset-password"]),
        (cmd_mfa_enable, ["auth", "mfa", "enable", "--device-name", "x", "--browser"]),
        (cmd_mfa_disable, ["auth", "mfa", "disable", "--yes"]),
        (cmd_mfa_disable, ["auth", "mfa", "disable", "-y"]),
        (cmd_mfa_enroll, ["auth", "mfa", "enroll", "--browser"]),
        (cmd_mfa_confirm, ["auth", "mfa", "confirm", "123456", "--no-prefer"]),
        (cmd_mfa_confirm, ["auth", "mfa", "confirm", "123456", "--device-name", "x"]),
    ],
)
def test_leaf_commands_dispatch_to_their_handler(func, argv):
    args = build_parser().parse_args(argv)
    assert args.func is func


def test_shared_flags_accepted_in_trailing_position():
    args = build_parser().parse_args(
        ["auth", "status", "--otp", "123456", "--token-cache", "/tmp/tokens.json"]
    )
    assert args.otp == "123456"
    assert str(args.token_cache) == "/tmp/tokens.json"


def test_otp_command_option_parses():
    args = build_parser().parse_args(
        ["auth", "login", "--otp-command", "op item get Evnex --otp"]
    )
    assert args.otp_command == "op item get Evnex --otp"


def test_confirm_no_prefer_sets_prefer_false():
    args = build_parser().parse_args(
        ["auth", "mfa", "confirm", "123456", "--no-prefer"]
    )
    assert args.totp_code == "123456"
    assert args.prefer is False


def test_confirm_defaults_to_preferring():
    args = build_parser().parse_args(["auth", "mfa", "confirm", "123456"])
    assert args.prefer is True


def test_version_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--version"])
    assert exc.value.code == 0
    assert "evnex" in capsys.readouterr().out


def test_no_args_prints_help_and_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 0
    assert "usage:" in capsys.readouterr().out.lower()


@pytest.mark.parametrize(
    "argv",
    [
        ["auth", "signin"],
        ["auth", "enroll-totp"],
        ["auth", "confirm-totp", "123456"],
        ["auth", "--code", "123456", "status"],
    ],
)
def test_old_names_no_longer_parse(argv):
    with pytest.raises(SystemExit):
        build_parser().parse_args(argv)


@pytest.mark.parametrize("flag", ["--otp", "--otp-command", "--token-cache"])
def test_reset_password_rejects_session_flags(flag):
    # reset-password needs no session and no cache; the sign-in/cache flags
    # must be rejected rather than silently ignored.
    with pytest.raises(SystemExit):
        build_parser().parse_args(["auth", "reset-password", flag, "x"])


def test_logout_only_takes_token_cache():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["auth", "logout", "--otp", "123456"])


class TestLogout:
    # Driven via asyncio.run from sync tests so the filesystem setup happens
    # off the event loop (blockbuster forbids blocking I/O while it runs).
    def test_removes_present_cache(self, tmp_path, capsys):
        cache = tmp_path / "tokens.json"
        cache.write_text("{}")
        args = argparse.Namespace(token_cache=cache)

        asyncio.run(cmd_logout(args))

        assert not cache.exists()
        assert "Removed" in capsys.readouterr().out

    def test_missing_cache_reports_nothing_to_do(self, tmp_path, capsys):
        cache = tmp_path / "tokens.json"
        args = argparse.Namespace(token_cache=cache)

        asyncio.run(cmd_logout(args))

        assert "No cached session" in capsys.readouterr().out


class TestLoadTokens:
    def test_corrupt_cache_warns_and_returns_none(self, tmp_path, capsys):
        cache = tmp_path / "tokens.json"
        cache.write_text("not valid json {{{")

        assert _load_tokens(cache) is None
        assert "unreadable" in capsys.readouterr().err.lower()

    def test_missing_cache_returns_none(self, tmp_path):
        assert _load_tokens(tmp_path / "absent.json") is None


class TestPasswordMismatch:
    def test_change_password_mismatch_exits_1(self, monkeypatch, capsys):
        class FakeAuth:
            async def change_password(self, *args):
                raise AssertionError("password change must not run after mismatch")

        async def fake_signed_in(args):
            return FakeAuth()

        monkeypatch.setattr("evnex.cli._auth.signed_in_auth", fake_signed_in)
        prompts = iter(["current", "new-one", "new-two"])
        monkeypatch.setattr("getpass.getpass", lambda *a, **k: next(prompts))

        with pytest.raises(SystemExit) as exc:
            main(["auth", "change-password"])

        assert exc.value.code == 1
        assert "did not match" in capsys.readouterr().err.lower()

    def test_reset_password_mismatch_exits_1(self, monkeypatch, capsys):
        class FakeAuth:
            def __init__(self, *args, **kwargs):
                pass

            async def start_password_reset(self, username):
                return ""

            async def confirm_password_reset(self, *args):
                raise AssertionError("reset must not run after mismatch")

        monkeypatch.setattr("evnex.cli._auth.EvnexAuth", FakeAuth)
        monkeypatch.setenv("EVNEX_CLIENT_USERNAME", "user@example.com")
        monkeypatch.setattr("builtins.input", lambda *a, **k: "123456")
        prompts = iter(["new-one", "new-two"])
        monkeypatch.setattr("getpass.getpass", lambda *a, **k: next(prompts))

        with pytest.raises(SystemExit) as exc:
            main(["auth", "reset-password"])

        assert exc.value.code == 1
        assert "did not match" in capsys.readouterr().err.lower()


class TestOtpCommand:
    challenge = AuthChallenge(name="SOFTWARE_TOKEN_MFA", session="s", username="u")

    def test_failure_exits_1_and_reports(self, capsys):
        args = argparse.Namespace(otp=None, otp_command="echo boom >&2; exit 3")

        with pytest.raises(SystemExit) as exc:
            asyncio.run(_challenge_code(args, self.challenge))

        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "boom" in err
        assert "otp-command failed (exit 3)" in err

    def test_empty_output_exits_1(self, capsys):
        args = argparse.Namespace(otp=None, otp_command="true")

        with pytest.raises(SystemExit) as exc:
            asyncio.run(_challenge_code(args, self.challenge))

        assert exc.value.code == 1
        assert "no code" in capsys.readouterr().err.lower()

    def test_success_returns_stripped_code(self):
        args = argparse.Namespace(otp=None, otp_command="echo 123456")

        assert asyncio.run(_challenge_code(args, self.challenge)) == "123456"
