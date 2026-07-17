"""Smoke tests for the CLI parser wiring."""

import pytest

from evnex.cli import HANDLERS, build_parser, main


@pytest.mark.parametrize(
    "handler, argv",
    [
        ("login", ["auth", "login"]),
        ("login", ["auth", "login", "--otp", "123456"]),
        ("logout", ["auth", "logout"]),
        ("status", ["auth", "status"]),
        ("change-password", ["auth", "change-password"]),
        ("reset-password", ["auth", "reset-password"]),
        ("mfa-enable", ["auth", "mfa", "enable", "--device-name", "x", "--browser"]),
        ("mfa-disable", ["auth", "mfa", "disable", "--yes"]),
        ("mfa-disable", ["auth", "mfa", "disable", "-y"]),
        ("mfa-enroll", ["auth", "mfa", "enroll", "--browser"]),
        ("mfa-confirm", ["auth", "mfa", "confirm", "123456", "--no-prefer"]),
        ("mfa-confirm", ["auth", "mfa", "confirm", "123456", "--device-name", "x"]),
    ],
)
def test_leaf_commands_parse_and_have_handlers(handler, argv):
    args = build_parser().parse_args(argv)
    assert args.handler == handler
    assert handler in HANDLERS


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
