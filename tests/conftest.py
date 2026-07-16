import time
from unittest.mock import DEFAULT, MagicMock

import pytest
from blockbuster import blockbuster_ctx
from tenacity import wait_none

from evnex.api import Evnex


class FakeCognito:
    """Offline stand-in for pycognito.Cognito: every method is a MagicMock.

    Each method briefly sleeps, mimicking pycognito's blocking network I/O,
    so blockbuster fails any test where one runs on the event loop instead
    of in a worker thread.
    """

    def __init__(
        self,
        user_pool_id=None,
        client_id=None,
        username=None,
        id_token=None,
        refresh_token=None,
        access_token=None,
    ):
        self.username = username
        self.id_token = id_token
        self.refresh_token = refresh_token
        self.access_token = access_token
        self.mfa_tokens = None
        self._token_serial = 0
        self.authenticate = MagicMock(side_effect=self._issue_tokens)
        self.check_token = MagicMock(return_value=False, side_effect=self._block)
        self.renew_access_token = MagicMock(side_effect=self._rotate_tokens)
        self.verify_tokens = MagicMock(side_effect=self._block)
        self.respond_to_sms_mfa_challenge = MagicMock(side_effect=self._block)
        self.respond_to_software_token_mfa_challenge = MagicMock(
            side_effect=self._block
        )

    @staticmethod
    def _block(*args, **kwargs):
        time.sleep(0.001)
        return DEFAULT

    def _issue_tokens(self, password=None):
        self._block()
        self.access_token = "access-0"
        self.id_token = "id-0"
        self.refresh_token = "refresh-0"

    def _rotate_tokens(self):
        self._block()
        self._token_serial += 1
        self.access_token = f"access-{self._token_serial}"
        self.id_token = f"id-{self._token_serial}"


@pytest.fixture(autouse=True)
def detect_blocking_calls():
    with blockbuster_ctx() as bb:
        yield bb


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr("evnex.api.Cognito", FakeCognito)
    monkeypatch.setattr(Evnex.get_user_detail.retry, "wait", wait_none())


@pytest.fixture
def client():
    return Evnex(username="user@example.com", password="hunter2")


@pytest.fixture
def authenticated_client(client):
    client.cognito._issue_tokens()
    return client


@pytest.fixture
def resumed_client():
    return Evnex(
        username="user@example.com",
        password="hunter2",
        id_token="id-0",
        access_token="access-0",
        refresh_token="refresh-0",
    )
