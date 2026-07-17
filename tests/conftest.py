import time
from datetime import UTC, datetime, timedelta
from unittest.mock import DEFAULT, MagicMock

import jwt
import pytest
from blockbuster import blockbuster_ctx
from tenacity import wait_none

from evnex.api import Evnex
from evnex.auth import EvnexAuth, TokenSet


def make_jwt(expires_in: timedelta = timedelta(hours=24)) -> str:
    """A structurally valid (unverified) access token with an exp claim."""
    exp = datetime.now(tz=UTC) + expires_in
    return jwt.encode(
        {"exp": int(exp.timestamp())},
        key="test-key-long-enough-for-hs256-minimum!",
        algorithm="HS256",
    )


class FakeCognito:
    """Offline stand-in for pycognito.Cognito: every method is a MagicMock.

    Each method briefly sleeps, mimicking pycognito's blocking network I/O,
    so blockbuster fails any test where one runs on the event loop instead
    of in a worker thread. Construction also blocks, mimicking boto3 client
    setup.
    """

    def __init__(self, user_pool_id=None, client_id=None, username=None, **kwargs):
        time.sleep(0.001)
        self.username = username
        self.id_token = None
        self.refresh_token = None
        self.access_token = None
        self._token_serial = 0
        self.authenticate = MagicMock(side_effect=self._issue_tokens)
        self.renew_access_token = MagicMock(side_effect=self._rotate_tokens)
        self.respond_to_software_token_mfa_challenge = MagicMock(
            side_effect=lambda code, mfa_tokens=None: self._issue_tokens()
        )
        self.respond_to_sms_mfa_challenge = MagicMock(
            side_effect=lambda code, mfa_tokens=None: self._issue_tokens()
        )
        self.associate_software_token = MagicMock(
            side_effect=self._block, return_value="FAKESECRETBASE32"
        )
        self.verify_software_token = MagicMock(
            side_effect=self._block, return_value=True
        )
        self.set_user_mfa_preference = MagicMock(side_effect=self._block)
        self.client = MagicMock()
        self.client.get_user = MagicMock(
            side_effect=self._block,
            return_value={
                "UserMFASettingList": ["SOFTWARE_TOKEN_MFA"],
                "PreferredMfaSetting": "SOFTWARE_TOKEN_MFA",
            },
        )

    @staticmethod
    def _block(*args, **kwargs):
        time.sleep(0.001)
        return DEFAULT

    def _issue_tokens(self, password=None):
        self._block()
        self._token_serial += 1
        self.access_token = f"access-{self._token_serial}"
        self.id_token = f"id-{self._token_serial}"
        self.refresh_token = "refresh-0"

    def _rotate_tokens(self):
        self._block()
        self._token_serial += 1
        self.access_token = f"access-{self._token_serial}"
        self.id_token = f"id-{self._token_serial}"
        # Cognito renewals do not return a refresh token unless rotation
        # is enabled on the pool
        self.refresh_token = None


@pytest.fixture(autouse=True)
def detect_blocking_calls():
    with blockbuster_ctx() as bb:
        yield bb


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr("evnex.auth.Cognito", FakeCognito)
    monkeypatch.setattr(Evnex.get_user_detail.retry, "wait", wait_none())


@pytest.fixture
def token_updates():
    """Records every TokenSet published via on_token_update."""
    return []


@pytest.fixture
def auth(token_updates):
    async def record(tokens: TokenSet) -> None:
        token_updates.append(tokens)

    return EvnexAuth(on_token_update=record)


@pytest.fixture
def resumed_auth(token_updates):
    """An EvnexAuth resumed from persisted tokens (no credentials)."""

    async def record(tokens: TokenSet) -> None:
        token_updates.append(tokens)

    return EvnexAuth(
        tokens=TokenSet(
            access_token="access-0",
            id_token="id-0",
            refresh_token="refresh-0",
        ),
        on_token_update=record,
    )


@pytest.fixture
def client(resumed_auth):
    return Evnex(auth=resumed_auth)
