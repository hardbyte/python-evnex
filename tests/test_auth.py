"""Tests for authentication, token refresh, MFA and retry behaviour.

Cognito is replaced with an offline fake (see conftest.FakeCognito); HTTP
calls are mocked with respx; blockbuster fails any test that runs blocking
I/O on the event loop.
"""

import asyncio

import botocore.exceptions
import httpx
import pytest
import respx
from pycognito.exceptions import (
    SoftwareTokenMFAChallengeException,
    TokenVerificationException,
)

from evnex.api import Evnex
from evnex.errors import NotAuthorizedException

USER_URL = "https://client-api.evnex.io/v2/apps/user"

USER_PAYLOAD = {
    "data": {
        "id": "b102b5e3-2b00-4f6b-9b0c-b579c609f969",
        "createdDate": "2022-01-01T00:00:00Z",
        "updatedDate": "2022-01-01T00:00:00Z",
        "name": "Test User",
        "email": "user@example.com",
        "organisations": [
            {
                "id": "org-1",
                "isDefault": True,
                "role": 1,
                "createdDate": "2022-01-01T00:00:00Z",
                "name": "Home",
                "slug": "home",
                "tier": 1,
                "updatedDate": "2022-01-01T00:00:00Z",
            }
        ],
    }
}


class TestFirstUseAuthentication:
    async def test_first_call_authenticates_automatically(self, client):
        with respx.mock:
            respx.get(USER_URL).mock(
                return_value=httpx.Response(200, json=USER_PAYLOAD)
            )
            user = await client.get_user_detail()

        assert user.name == "Test User"
        client.cognito.authenticate.assert_called_once()

    async def test_auth_failure_raises_immediately(self, client):
        client.cognito.authenticate.side_effect = botocore.exceptions.ClientError(
            {"Error": {"Code": "NotAuthorizedException", "Message": "Bad creds"}},
            "InitiateAuth",
        )

        with pytest.raises(NotAuthorizedException):
            await client.get_user_detail()
        # Never retried: auth errors would lock accounts and can't self-heal
        client.cognito.authenticate.assert_called_once()

    async def test_mfa_challenge_propagates_without_retry(self, client):
        client.cognito.authenticate.side_effect = SoftwareTokenMFAChallengeException(
            "MFA challenge", {"ChallengeName": "SOFTWARE_TOKEN_MFA", "Session": "s"}
        )

        with pytest.raises(SoftwareTokenMFAChallengeException):
            await client.get_user_detail()
        client.cognito.authenticate.assert_called_once()

    async def test_concurrent_first_calls_authenticate_once(self, client):
        with respx.mock:
            respx.get(USER_URL).mock(
                return_value=httpx.Response(200, json=USER_PAYLOAD)
            )
            await asyncio.gather(client.get_user_detail(), client.get_user_detail())

        client.cognito.authenticate.assert_called_once()


class TestTokenRefresh:
    async def test_expired_token_renewed_before_request(self, authenticated_client):
        authenticated_client.cognito.check_token.return_value = True

        with respx.mock:
            respx.get(USER_URL).mock(
                return_value=httpx.Response(200, json=USER_PAYLOAD)
            )
            user = await authenticated_client.get_user_detail()

        assert user.name == "Test User"
        authenticated_client.cognito.check_token.assert_called_once()

    async def test_refresh_failure_wrapped_and_not_retried(self, authenticated_client):
        authenticated_client.cognito.check_token.side_effect = (
            botocore.exceptions.ClientError(
                {
                    "Error": {
                        "Code": "NotAuthorized",
                        "Message": "Refresh token expired",
                    }
                },
                "InitiateAuth",
            )
        )

        with pytest.raises(NotAuthorizedException):
            await authenticated_client.get_user_detail()
        authenticated_client.cognito.check_token.assert_called_once()

    async def test_refresh_token_only_resumption(self):
        client = Evnex(
            username="user@example.com", password="hunter2", refresh_token="refresh-0"
        )

        def do_renew():
            client.cognito.access_token = "access-1"
            client.cognito.id_token = "id-1"

        client.cognito.renew_access_token.side_effect = do_renew

        with respx.mock:
            respx.get(USER_URL).mock(
                return_value=httpx.Response(200, json=USER_PAYLOAD)
            )
            user = await client.get_user_detail()

        assert user.name == "Test User"
        client.cognito.renew_access_token.assert_called_once()
        client.cognito.authenticate.assert_not_called()


class TestResumedTokenVerification:
    async def test_resumed_tokens_verified_once_on_first_use(self, resumed_client):
        with respx.mock:
            respx.get(USER_URL).mock(
                return_value=httpx.Response(200, json=USER_PAYLOAD)
            )
            await resumed_client.get_user_detail()
            await resumed_client.get_user_detail()

        resumed_client.cognito.verify_tokens.assert_called_once()

    async def test_invalid_resumed_tokens_raise_not_authorized(self, resumed_client):
        resumed_client.cognito.verify_tokens.side_effect = TokenVerificationException(
            "bad signature"
        )

        with pytest.raises(NotAuthorizedException):
            await resumed_client.get_user_detail()


class TestUnauthorizedResponses:
    async def test_401_with_renewed_token_retries_request(self, authenticated_client):
        # Valid before the first request; found expired+renewed after the 401
        authenticated_client.cognito.check_token.side_effect = [False, True, False]

        with respx.mock:
            route = respx.get(USER_URL)
            route.side_effect = [
                httpx.Response(401),
                httpx.Response(200, json=USER_PAYLOAD),
            ]
            user = await authenticated_client.get_user_detail()

        assert user.name == "Test User"
        assert route.call_count == 2

    async def test_401_with_valid_token_fails_without_retry(self, authenticated_client):
        with respx.mock:
            route = respx.get(USER_URL).mock(return_value=httpx.Response(401))
            with pytest.raises(NotAuthorizedException):
                await authenticated_client.get_user_detail()

        assert route.call_count == 1

    async def test_persistent_401_despite_renewals_exhausts_retries(
        self, authenticated_client
    ):
        authenticated_client.cognito.check_token.return_value = True

        with respx.mock:
            route = respx.get(USER_URL).mock(return_value=httpx.Response(401))
            with pytest.raises(NotAuthorizedException):
                await authenticated_client.get_user_detail()

        # Bounded by stop_after_attempt(5), not an endless refresh loop
        assert route.call_count == 5


class TestMFAChallengeResponse:
    def test_invalid_mode_raises_value_error(self, client):
        with pytest.raises(ValueError, match="Unknown MFA mode"):
            client.respond_to_mfa_challenge("123456", "sms")

    def test_totp_mode_delegates_with_mfa_tokens(self, client):
        client.respond_to_mfa_challenge("123456", "TOTP", mfa_tokens={"Session": "s"})
        client.cognito.respond_to_software_token_mfa_challenge.assert_called_once_with(
            "123456", mfa_tokens={"Session": "s"}
        )

    def test_rejected_code_raises_not_authorized(self, client):
        client.cognito.respond_to_sms_mfa_challenge.side_effect = (
            botocore.exceptions.ClientError(
                {"Error": {"Code": "CodeMismatchException", "Message": "Wrong code"}},
                "RespondToAuthChallenge",
            )
        )
        with pytest.raises(NotAuthorizedException):
            client.respond_to_mfa_challenge("123456", "SMS")
