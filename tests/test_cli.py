"""Smoke tests for the CLI parser wiring."""

import pytest

from evnex.cli import HANDLERS, build_parser


def test_all_auth_commands_parse_and_have_handlers():
    parser = build_parser()
    for command, argv in [
        ("status", ["auth", "status"]),
        ("signin", ["auth", "signin"]),
        ("enroll-totp", ["auth", "enroll-totp", "--browser"]),
        ("confirm-totp", ["auth", "confirm-totp", "123456", "--device-name", "x"]),
        ("disable", ["auth", "disable", "--yes"]),
    ]:
        args = parser.parse_args(argv)
        assert args.auth_command == command
        assert command in HANDLERS


def test_code_command_option_parses():
    args = build_parser().parse_args(
        ["auth", "--code-command", "op item get Evnex --otp", "status"]
    )
    assert args.code_command == "op item get Evnex --otp"


def test_missing_subcommand_errors():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["auth"])
