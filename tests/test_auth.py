"""Tests for the EvnexAuth token lifecycle and transport-level 401 recovery.

Cognito is replaced with an offline fake (see conftest.FakeCognito); HTTP
calls are mocked with respx; blockbuster fails any test that runs blocking
I/O on the event loop.
"""

import asyncio
from datetime import timedelta

import botocore.exceptions
import httpx
import pytest
import respx
from pycognito.exceptions import SoftwareTokenMFAChallengeException

from evnex.api import Evnex
from evnex.auth import (
    CHALLENGE_SOFTWARE_TOKEN_MFA,
    AuthChallenge,
    EvnexAuth,
    TokenSet,
)
from evnex.errors import (
    ChallengeExpiredError,
    EvnexAuthError,
    InvalidChallengeResponseError,
    InvalidCredentialsError,
    NotAuthorizedException,
    ReauthenticationRequiredError,
)

from .conftest import make_jwt

USER_URL = "https://client-api.evnex.io/v2/apps/user"

USER_PAYLOAD = {
    "data": {
        "id": "b102b5e3-2b00-4f6b-9b0c-b579c609f969",
        "createdDate": "2022-01-01T00:00:00Z",
        "updatedDate": "2022-01-01T00:00:00Z",
        "name": "Test User",
        "email": "user@example.com",
        "organisations": [],
    }
}


def client_error(code: str, message: str = "nope") -> botocore.exceptions.ClientError:
    return botocore.exceptions.ClientError(
        {"Error": {"Code": code, "Message": message}}, "SomeOperation"
    )


def mfa_challenge_exception() -> SoftwareTokenMFAChallengeException:
    return SoftwareTokenMFAChallengeException(
        "Do Software Token MFA",
        {
            "ChallengeName": CHALLENGE_SOFTWARE_TOKEN_MFA,
            "Session": "opaque-session",
            "ChallengeParameters": {"FRIENDLY_DEVICE_NAME": "My TOTP device"},
        },
    )


class TestTokenSet:
    def test_round_trips_through_dict(self):
        tokens = TokenSet(
            access_token=make_jwt(), id_token="id", refresh_token="refresh"
        )
        restored = TokenSet.from_dict(tokens.to_dict())
        assert restored == tokens

    def test_expiry_derived_from_jwt_when_missing(self):
        data = {"access_token": make_jwt(timedelta(hours=1)), "refresh_token": "r"}
        tokens = TokenSet.from_dict(data)
        assert tokens.expires_at is not None

    def test_repr_redacts_tokens(self):
        tokens = TokenSet(access_token="secret-access", refresh_token="secret-refresh")
        assert "secret" not in repr(tokens)


class TestAuthChallenge:
    def test_round_trips_through_dict(self):
        challenge = AuthChallenge(
            name=CHALLENGE_SOFTWARE_TOKEN_MFA,
            session="s",
            username="user@example.com",
            parameters={"FRIENDLY_DEVICE_NAME": "phone"},
        )
        assert AuthChallenge.from_dict(challenge.to_dict()) == challenge


class TestInteractiveAuthentication:
    async def test_password_only_success(self, auth, token_updates):
        result = await auth.start_authentication("user@example.com", "hunter2")

        assert isinstance(result, TokenSet)
        assert auth.tokens is result
        assert token_updates == [result]

    async def test_mfa_challenge_returned(self, auth, token_updates):
        # First call builds the fake cognito so the side effect can be set
        await auth.start_authentication("user@example.com", "hunter2")
        auth._cognito.authenticate.side_effect = mfa_challenge_exception()
        auth._tokens = None
        token_updates.clear()

        challenge = await auth.start_authentication("user@example.com", "hunter2")

        assert isinstance(challenge, AuthChallenge)
        assert challenge.name == CHALLENGE_SOFTWARE_TOKEN_MFA
        assert challenge.username == "user@example.com"
        assert challenge.parameters["FRIENDLY_DEVICE_NAME"] == "My TOTP device"
        assert auth.tokens is None
        assert token_updates == []

    async def test_challenge_response_issues_tokens(self, auth, token_updates):
        challenge = AuthChallenge(
            name=CHALLENGE_SOFTWARE_TOKEN_MFA,
            session="opaque-session",
            username="user@example.com",
        )
        result = await auth.respond_to_challenge(challenge, "123456")

        assert isinstance(result, TokenSet)
        assert auth.tokens is result
        assert token_updates == [result]
        auth._cognito.respond_to_software_token_mfa_challenge.assert_called_once_with(
            "123456",
            mfa_tokens={
                "ChallengeName": CHALLENGE_SOFTWARE_TOKEN_MFA,
                "Session": "opaque-session",
            },
        )

    async def test_wrong_code_raises_invalid_response(self, auth):
        challenge = AuthChallenge(
            name=CHALLENGE_SOFTWARE_TOKEN_MFA, session="s", username="u"
        )
        await auth.start_authentication("u", "p")  # builds the fake cognito
        auth._cognito.respond_to_software_token_mfa_challenge.side_effect = (
            client_error("CodeMismatchException")
        )

        with pytest.raises(InvalidChallengeResponseError):
            await auth.respond_to_challenge(challenge, "000000")

    async def test_expired_session_raises_challenge_expired(self, auth):
        challenge = AuthChallenge(
            name=CHALLENGE_SOFTWARE_TOKEN_MFA, session="s", username="u"
        )
        await auth.start_authentication("u", "p")
        auth._cognito.respond_to_software_token_mfa_challenge.side_effect = (
            client_error("NotAuthorizedException", "Invalid session for the user")
        )

        with pytest.raises(ChallengeExpiredError):
            await auth.respond_to_challenge(challenge, "123456")

    async def test_unsupported_challenge_type(self, auth):
        challenge = AuthChallenge(
            name="NEW_PASSWORD_REQUIRED", session="s", username="u"
        )

        with pytest.raises(EvnexAuthError, match="NEW_PASSWORD_REQUIRED"):
            await auth.respond_to_challenge(challenge, "irrelevant")

    async def test_invalid_credentials(self, auth):
        await auth.start_authentication("u", "p")
        auth._cognito.authenticate.side_effect = client_error(
            "NotAuthorizedException", "Incorrect username or password."
        )

        with pytest.raises(InvalidCredentialsError):
            await auth.start_authentication("u", "wrong")

    async def test_auth_errors_are_not_authorized_exceptions(self):
        # Compat: existing handlers catch NotAuthorizedException
        assert issubclass(InvalidCredentialsError, NotAuthorizedException)
        assert issubclass(ReauthenticationRequiredError, NotAuthorizedException)


class TestTokenLifecycle:
    async def test_resume_needs_no_credentials(self, resumed_auth):
        token = await resumed_auth.get_access_token()
        assert token == "access-0"

    async def test_no_session_raises_reauthentication_required(self, auth):
        with pytest.raises(ReauthenticationRequiredError):
            await auth.get_access_token()

    async def test_expired_token_refreshed_proactively(self):
        expired = make_jwt(timedelta(seconds=-60))
        auth = EvnexAuth(
            tokens=TokenSet(access_token=expired, refresh_token="refresh-0")
        )
        token = await auth.get_access_token()

        assert token == "access-1"
        assert auth.tokens.refresh_token == "refresh-0"  # carried forward

    async def test_force_refresh_single_flight(self, resumed_auth):
        results = await asyncio.gather(
            resumed_auth.force_refresh(stale_access_token="access-0"),
            resumed_auth.force_refresh(stale_access_token="access-0"),
        )

        assert results[0] == results[1]
        resumed_auth._cognito.renew_access_token.assert_called_once()

    async def test_refresh_without_refresh_token(self):
        auth = EvnexAuth(tokens=TokenSet(access_token="access-0"))
        with pytest.raises(ReauthenticationRequiredError):
            await auth.force_refresh(stale_access_token="access-0")

    async def test_callback_failure_does_not_break_auth(self, caplog):
        async def failing_callback(tokens: TokenSet) -> None:
            raise RuntimeError("disk full")

        auth = EvnexAuth(on_token_update=failing_callback)
        result = await auth.start_authentication("u", "p")

        assert isinstance(result, TokenSet)
        assert auth.tokens is result
        assert "Token update callback failed" in caplog.text


class TestTransport:
    async def test_request_carries_access_token(self, client):
        with respx.mock:
            route = respx.get(USER_URL).mock(
                return_value=httpx.Response(200, json=USER_PAYLOAD)
            )
            user = await client.get_user_detail()

        assert user.name == "Test User"
        assert route.calls[0].request.headers["Authorization"] == "access-0"

    async def test_401_refreshes_and_resends_once(self, client, token_updates):
        def respond(request):
            if request.headers["Authorization"] == "access-0":
                return httpx.Response(401)
            return httpx.Response(200, json=USER_PAYLOAD)

        with respx.mock:
            route = respx.get(USER_URL).mock(side_effect=respond)
            user = await client.get_user_detail()

        assert user.name == "Test User"
        assert route.call_count == 2
        assert route.calls[1].request.headers["Authorization"] == "access-1"
        # The rotated tokens were published before the resend completed
        assert token_updates and token_updates[0].access_token == "access-1"

    async def test_persistent_401_not_retried_by_tenacity(self, client):
        with respx.mock:
            route = respx.get(USER_URL).mock(return_value=httpx.Response(401))
            with pytest.raises(ReauthenticationRequiredError):
                await client.get_user_detail()

        # One original request + exactly one auth-flow resend; the generic
        # retry policy must not multiply auth recovery
        assert route.call_count == 2

    async def test_no_tokens_fails_before_any_request(self, auth):
        client = Evnex(auth=auth)
        with respx.mock:
            route = respx.get(USER_URL).mock(
                return_value=httpx.Response(200, json=USER_PAYLOAD)
            )
            with pytest.raises(ReauthenticationRequiredError):
                await client.get_user_detail()

        assert route.call_count == 0

    async def test_concurrent_401s_refresh_once(self, client):
        def respond(request):
            if request.headers["Authorization"] == "access-0":
                return httpx.Response(401)
            return httpx.Response(200, json=USER_PAYLOAD)

        with respx.mock:
            respx.get(USER_URL).mock(side_effect=respond)
            users = await asyncio.gather(
                client.get_user_detail(), client.get_user_detail()
            )

        assert all(user.name == "Test User" for user in users)
        client.auth._cognito.renew_access_token.assert_called_once()


class TestReviewRegressions:
    """Regressions for the PR #114 review findings."""

    async def test_refresh_token_only_token_set(self):
        # Constructible without an access token, including via from_dict
        tokens = TokenSet(refresh_token="refresh-0")
        assert tokens.access_token is None
        assert TokenSet.from_dict({"refresh_token": "refresh-0"}) == tokens

        auth = EvnexAuth(tokens=tokens)
        assert await auth.get_access_token() == "access-1"

    async def test_refresh_only_resume_makes_no_wasted_request(self):
        auth = EvnexAuth(tokens=TokenSet(refresh_token="refresh-0"))
        client = Evnex(auth=auth)

        with respx.mock:
            route = respx.get(USER_URL).mock(
                return_value=httpx.Response(200, json=USER_PAYLOAD)
            )
            await client.get_user_detail()

        # The refresh happened before the request, not via a 401 round-trip
        assert route.call_count == 1
        assert route.calls[0].request.headers["Authorization"] == "access-1"

    async def test_concurrent_refresh_only_startup_single_flight(self):
        auth = EvnexAuth(tokens=TokenSet(refresh_token="refresh-0"))
        client = Evnex(auth=auth)

        with respx.mock:
            respx.get(USER_URL).mock(
                return_value=httpx.Response(200, json=USER_PAYLOAD)
            )
            await asyncio.gather(client.get_user_detail(), client.get_user_detail())

        auth._cognito.renew_access_token.assert_called_once()

    async def test_tokens_published_only_after_persistence(self):
        gate = asyncio.Event()
        observed_during_persist = []

        async def slow_save(tokens: TokenSet) -> None:
            observed_during_persist.append(auth.tokens)
            await gate.wait()

        auth = EvnexAuth(
            tokens=TokenSet(refresh_token="refresh-0"), on_token_update=slow_save
        )
        refresh = asyncio.create_task(auth.force_refresh())
        while not observed_during_persist:
            await asyncio.sleep(0)

        # Mid-persistence, other tasks must still see the old token set
        assert observed_during_persist[0] is not None
        assert observed_during_persist[0].access_token is None
        assert auth.tokens.access_token is None

        gate.set()
        new_tokens = await refresh
        assert auth.tokens is new_tokens

    async def test_retry_exhaustion_reraises_underlying_error(self, client):
        with respx.mock:
            route = respx.get(USER_URL).mock(
                side_effect=httpx.ConnectError("connection refused")
            )
            with pytest.raises(httpx.ConnectError):
                await client.get_user_detail()

        assert route.call_count == 5  # stop_after_attempt, then reraise

    async def test_override_persistent_401_not_retried(self, resumed_auth, monkeypatch):
        from tenacity import wait_none

        client = Evnex(auth=resumed_auth)
        monkeypatch.setattr(Evnex.set_charge_point_override.retry, "wait", wait_none())
        override_url = (
            "https://client-api.evnex.io/charge-points/cp-1/commands/set-override"
        )

        with respx.mock:
            route = respx.post(override_url).mock(return_value=httpx.Response(401))
            with pytest.raises(ReauthenticationRequiredError):
                await client.set_charge_point_override(
                    charge_point_id="cp-1", charge_now=True
                )

        # One original send + exactly one auth-flow resend; the command was
        # never submitted repeatedly
        assert route.call_count == 2
