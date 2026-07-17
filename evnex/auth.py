"""Async, persistence-aware authentication for the EVNEX Cloud API.

See docs/design/113-auth-refactor.md for the design rationale.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import botocore.exceptions
import httpx
import jwt
from pycognito import Cognito
from pycognito.exceptions import MFAChallengeException

from evnex.config import EvnexConfig
from evnex.errors import (
    ChallengeExpiredError,
    EvnexAuthError,
    InvalidChallengeResponseError,
    InvalidCredentialsError,
    ReauthenticationRequiredError,
)

logger = logging.getLogger("evnex.auth")

CHALLENGE_SOFTWARE_TOKEN_MFA = "SOFTWARE_TOKEN_MFA"
CHALLENGE_SMS_MFA = "SMS_MFA"

# Refresh this long before the access token's recorded expiry, to absorb
# clock skew between us and the API
EXPIRY_SKEW = timedelta(seconds=30)

TokenUpdateCallback = Callable[["TokenSet"], Awaitable[None]]

# Sentinel distinguishing "always refresh" from "refresh unless the tokens
# already rotated past this stale access token (which may be None)"
_ALWAYS_REFRESH: Any = object()


def _decode_expiry(access_token: str) -> datetime | None:
    """Best-effort read of the JWT exp claim; the server stays authoritative."""
    try:
        claims = jwt.decode(access_token, options={"verify_signature": False})
        return datetime.fromtimestamp(claims["exp"], tz=UTC)
    except (jwt.DecodeError, KeyError, TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class TokenSet:
    """An immutable set of session tokens, safe to persist and to log."""

    access_token: str | None = field(repr=False, default=None)
    id_token: str | None = field(repr=False, default=None)
    refresh_token: str | None = field(repr=False, default=None)
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.expires_at is None and self.access_token:
            object.__setattr__(self, "expires_at", _decode_expiry(self.access_token))

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "id_token": self.id_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TokenSet:
        expires_at = data.get("expires_at")
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at)
        return cls(
            access_token=data.get("access_token"),
            id_token=data.get("id_token"),
            refresh_token=data.get("refresh_token"),
            expires_at=expires_at,
        )


@dataclass(frozen=True, slots=True)
class AuthChallenge:
    """A pending Cognito authentication challenge.

    JSON-serializable via to_dict/from_dict so a challenge can be answered
    by a different process or a later request (within the short session
    lifetime, around 3 minutes).
    """

    name: str
    session: str = field(repr=False)
    username: str
    parameters: Mapping[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "session": self.session,
            "username": self.username,
            "parameters": dict(self.parameters),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AuthChallenge:
        return cls(
            name=data["name"],
            session=data["session"],
            username=data["username"],
            parameters=dict(data.get("parameters") or {}),
        )


class EvnexAuth:
    """Owns the Cognito session lifecycle for the EVNEX API.

    Holds no username or password: interactive credentials are arguments to
    start_authentication() only. Resume a session by passing tokens (a
    refresh token alone is enough). Every newly issued token set is passed
    to on_token_update before any request proceeds with it.
    """

    def __init__(
        self,
        *,
        tokens: TokenSet | None = None,
        on_token_update: TokenUpdateCallback | None = None,
        config: EvnexConfig | None = None,
    ) -> None:
        self._config = config or EvnexConfig()
        self._tokens = tokens
        self._on_token_update = on_token_update
        self._lock = asyncio.Lock()
        # Built lazily inside a worker thread: boto3 client construction
        # performs blocking I/O (credential lookup)
        self._cognito: Cognito | None = None

    @property
    def tokens(self) -> TokenSet | None:
        """The current token set, if any."""
        return self._tokens

    async def start_authentication(
        self, username: str, password: str
    ) -> TokenSet | AuthChallenge:
        """Begin interactive sign-in with the user's credentials.

        Returns a TokenSet on immediate success, or an AuthChallenge that
        must be answered via respond_to_challenge().

        :raises InvalidCredentialsError: the credentials were rejected
        """

        def _authenticate() -> TokenSet | AuthChallenge:
            cognito = self._ensure_cognito()
            cognito.username = username
            try:
                cognito.authenticate(password=password)
            except MFAChallengeException as challenge:
                payload = challenge.get_tokens()
                return AuthChallenge(
                    name=payload["ChallengeName"],
                    session=payload["Session"],
                    username=username,
                    parameters=dict(payload.get("ChallengeParameters") or {}),
                )
            return self._tokens_from_cognito(cognito)

        async with self._lock:
            try:
                result = await asyncio.to_thread(_authenticate)
            except botocore.exceptions.ClientError as err:
                raise InvalidCredentialsError(_error_message(err)) from err
            if isinstance(result, TokenSet):
                await self._store_tokens(result)
            return result

    async def respond_to_challenge(
        self, challenge: AuthChallenge, response: str
    ) -> TokenSet | AuthChallenge:
        """Answer an authentication challenge (e.g. with a 6-digit MFA code).

        :raises InvalidChallengeResponseError: the code was rejected; the same
            challenge may be retried with a new code
        :raises ChallengeExpiredError: the challenge session lapsed; call
            start_authentication() again
        :raises EvnexAuthError: the challenge type is not supported
        """

        def _respond() -> TokenSet:
            cognito = self._ensure_cognito()
            cognito.username = challenge.username
            mfa_tokens = {
                "ChallengeName": challenge.name,
                "Session": challenge.session,
            }
            if challenge.name == CHALLENGE_SOFTWARE_TOKEN_MFA:
                cognito.respond_to_software_token_mfa_challenge(
                    response.strip(), mfa_tokens=mfa_tokens
                )
            elif challenge.name == CHALLENGE_SMS_MFA:
                cognito.respond_to_sms_mfa_challenge(
                    response.strip(), mfa_tokens=mfa_tokens
                )
            else:
                raise EvnexAuthError(
                    f"Unsupported authentication challenge {challenge.name!r}"
                )
            return self._tokens_from_cognito(cognito)

        async with self._lock:
            try:
                tokens = await asyncio.to_thread(_respond)
            except botocore.exceptions.ClientError as err:
                raise _map_challenge_error(err) from err
            await self._store_tokens(tokens)
            return tokens

    async def get_access_token(self) -> str:
        """Return a valid access token, refreshing the session if required.

        :raises ReauthenticationRequiredError: no usable session exists
        """
        tokens = self._tokens
        if tokens is None or tokens.access_token is None:
            refreshed = await self.force_refresh(
                stale_access_token=tokens.access_token if tokens else None
            )
            return self._require_access_token(refreshed)
        if tokens.expires_at is not None:
            now = datetime.now(tz=UTC)
            if now >= tokens.expires_at - EXPIRY_SKEW:
                refreshed = await self.force_refresh(
                    stale_access_token=tokens.access_token
                )
                return self._require_access_token(refreshed)
        return tokens.access_token

    @staticmethod
    def _require_access_token(tokens: TokenSet) -> str:
        if tokens.access_token is None:
            raise ReauthenticationRequiredError(
                "The refreshed session did not include an access token"
            )
        return tokens.access_token

    async def force_refresh(
        self, *, stale_access_token: str | None = _ALWAYS_REFRESH
    ) -> TokenSet:
        """Obtain fresh tokens using the refresh token.

        Single-flight: pass the access token that was rejected (possibly
        None for a session that never had one) as stale_access_token, and
        callers that lost the race return the already-rotated token set
        without refreshing again. Omit it to refresh unconditionally.

        :raises ReauthenticationRequiredError: no refresh token, or Cognito
            rejected it
        """
        async with self._lock:
            current = self._tokens
            if (
                current is not None
                and stale_access_token is not _ALWAYS_REFRESH
                and current.access_token != stale_access_token
            ):
                return current

            if current is None or current.refresh_token is None:
                raise ReauthenticationRequiredError(
                    "No session tokens; interactive authentication is required"
                )

            def _renew() -> TokenSet:
                cognito = self._ensure_cognito()
                cognito.access_token = current.access_token
                cognito.id_token = current.id_token
                cognito.refresh_token = current.refresh_token
                cognito.renew_access_token()
                return self._tokens_from_cognito(cognito)

            logger.debug("Refreshing session tokens")
            try:
                tokens = await asyncio.to_thread(_renew)
            except botocore.exceptions.ClientError as err:
                raise ReauthenticationRequiredError(_error_message(err)) from err
            await self._store_tokens(tokens)
            return tokens

    def _ensure_cognito(self) -> Cognito:
        """Build the pycognito client on first use. Runs in a worker thread."""
        if self._cognito is None:
            self._cognito = Cognito(
                user_pool_id=self._config.EVNEX_COGNITO_USER_POOL_ID,
                client_id=self._config.EVNEX_COGNITO_CLIENT_ID,
                username=None,
            )
        return self._cognito

    def _tokens_from_cognito(self, cognito: Cognito) -> TokenSet:
        return TokenSet(
            access_token=cognito.access_token,
            id_token=cognito.id_token,
            # Cognito omits the refresh token from renewals unless rotation
            # is enabled; carry the current one forward
            refresh_token=cognito.refresh_token
            or (self._tokens.refresh_token if self._tokens else None),
        )

    async def _store_tokens(self, tokens: TokenSet) -> None:
        """Persist, then publish, a new token set. Called with the lock held.

        The callback runs before the tokens become visible to other tasks
        (including get_access_token's unlocked fast path), so a token set can
        never be used for a request before the application has persisted it.
        """
        if self._on_token_update is not None:
            try:
                await self._on_token_update(tokens)
            except Exception:
                # A failing token store must not break API access; the
                # application can reconcile from .tokens at any time
                logger.exception("Token update callback failed")
        self._tokens = tokens


class EvnexHttpxAuth(httpx.Auth):
    """httpx auth flow: inject the access token; on 401 refresh and resend once.

    A 401 means the server rejected the request before executing it, so the
    single resend is safe even for command endpoints.
    """

    def __init__(self, auth: EvnexAuth) -> None:
        self._auth = auth

    async def async_auth_flow(self, request: httpx.Request):
        token = await self._auth.get_access_token()
        request.headers["Authorization"] = token
        response = yield request
        if response.status_code == 401:
            await self._auth.force_refresh(stale_access_token=token)
            request.headers["Authorization"] = await self._auth.get_access_token()
            yield request

    def sync_auth_flow(self, request: httpx.Request):
        raise RuntimeError("EvnexHttpxAuth only supports async clients")


def _error_message(err: botocore.exceptions.ClientError) -> str:
    return str(err.response.get("Error", {}).get("Message", err))


def _map_challenge_error(err: botocore.exceptions.ClientError) -> EvnexAuthError:
    code = err.response.get("Error", {}).get("Code", "")
    message = _error_message(err)
    if code == "CodeMismatchException":
        return InvalidChallengeResponseError(message)
    if code in ("ExpiredCodeException", "NotAuthorizedException"):
        # Cognito reports a lapsed challenge session as NotAuthorized
        return ChallengeExpiredError(message)
    return EvnexAuthError(message)
