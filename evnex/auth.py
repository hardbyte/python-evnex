"""Authentication and session lifecycle for the EVNEX Cloud API.

EvnexAuth signs in, answers MFA challenges, refreshes expired sessions, and
notifies the application whenever new tokens are issued so they can be
persisted. The Evnex client uses it to authenticate every request and to
recover transparently when the API rejects a token.

The underlying identity provider client (pycognito/boto3) is synchronous,
and no maintained async equivalent exists, so those calls run in worker
threads via asyncio.to_thread — nothing here blocks the event loop.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import botocore.exceptions
import httpx
import jwt
from pycognito import Cognito
from pycognito.exceptions import (
    ForceChangePasswordException,
    MFAChallengeException,
    TokenVerificationException,
    WarrantException,
)

from evnex.config import EvnexConfig
from evnex.errors import (
    ChallengeExpiredError,
    EvnexAuthError,
    InvalidChallengeResponseError,
    InvalidCredentialsError,
    PasswordChangeRequiredError,
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
        elif self.expires_at is not None and self.expires_at.tzinfo is None:
            # Treat naive datetimes from external stores as UTC so expiry
            # comparisons never raise
            object.__setattr__(self, "expires_at", self.expires_at.replace(tzinfo=UTC))

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
    username: str = field(repr=False)
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


@dataclass(frozen=True, slots=True)
class TotpEnrollment:
    """A pending TOTP device enrollment: load the secret, then confirm."""

    secret: str = field(repr=False)

    def provisioning_uri(self, account_name: str, issuer: str = "Evnex") -> str:
        """An otpauth:// URI for QR rendering or a password manager's OTP field.

        Matches the label/issuer conventions of EVNEX's own enrollment.
        """
        return (
            f"otpauth://totp/{quote(account_name)}"
            f"?secret={self.secret}&issuer={quote(issuer)}"
        )


@dataclass(frozen=True, slots=True)
class MfaStatus:
    """The MFA methods currently enabled for an account."""

    enabled: tuple[str, ...]
    preferred: str | None = None


class EvnexAuth:
    """Manages sign-in, MFA, and session renewal for an EVNEX account.

    Sign in interactively with start_authentication() (answering any
    AuthChallenge via respond_to_challenge()), or resume a previous session
    by passing tokens — a refresh token alone is enough. Expired sessions
    renew automatically; provide on_token_update to persist each newly
    issued token set, and it will have completed before any request uses
    the new tokens. Credentials themselves are never stored.
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
        # Serialises every operation that talks to Cognito or replaces
        # self._tokens: it makes refreshes single-flight, and it means the
        # mutable Cognito instance below is only ever used by one worker
        # thread at a time. It deliberately does NOT guard reads of
        # self._tokens — get_access_token's fast path reads the current
        # token set lock-free, which is safe because _store_tokens replaces
        # it with a single reference assignment, only after persistence.
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
            except ForceChangePasswordException as err:
                raise PasswordChangeRequiredError(
                    "Cognito requires a password change before sign-in"
                ) from err
            except WarrantException as err:
                # e.g. TokenVerificationException from pycognito verifying
                # the freshly issued tokens
                raise EvnexAuthError(str(err)) from err
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
            except TokenVerificationException as err:
                raise EvnexAuthError(str(err)) from err
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
            except WarrantException as err:
                # The renewed tokens failed pycognito's verification; the
                # session cannot be trusted. Network errors (requests/boto
                # connection failures) deliberately propagate: they are
                # transient and remain retryable.
                raise ReauthenticationRequiredError(str(err)) from err
            await self._store_tokens(tokens)
            return tokens

    async def get_mfa_status(self) -> MfaStatus:
        """Report which MFA methods are enabled for the signed-in account."""
        access_token = await self.get_access_token()

        def _get_user() -> MfaStatus:
            cognito = self._ensure_cognito()
            response = cognito.client.get_user(AccessToken=access_token)
            return MfaStatus(
                enabled=tuple(response.get("UserMFASettingList") or ()),
                preferred=response.get("PreferredMfaSetting"),
            )

        async with self._lock:
            try:
                return await asyncio.to_thread(_get_user)
            except botocore.exceptions.ClientError as err:
                raise EvnexAuthError(_error_message(err)) from err

    async def begin_totp_enrollment(self) -> TotpEnrollment:
        """Start enrolling a (new) TOTP authenticator device.

        Returns the shared secret to load into the authenticator — via QR
        code (see TotpEnrollment.provisioning_uri) or manual entry — then
        confirm with confirm_totp_enrollment(). Completing enrollment
        replaces any previously registered TOTP device.
        """
        access_token = await self.get_access_token()

        def _associate() -> TotpEnrollment:
            cognito = self._ensure_cognito()
            cognito.access_token = access_token
            return TotpEnrollment(secret=cognito.associate_software_token())

        async with self._lock:
            try:
                return await asyncio.to_thread(_associate)
            except botocore.exceptions.ClientError as err:
                raise EvnexAuthError(_error_message(err)) from err

    async def confirm_totp_enrollment(self, code: str, device_name: str = "") -> None:
        """Verify a code from the newly enrolled authenticator device.

        Note this registers the device but does not turn MFA on for the
        account; call set_mfa_preference(totp=True) as well when first
        enabling MFA.

        :raises InvalidChallengeResponseError: the code was rejected
        """
        access_token = await self.get_access_token()

        def _verify() -> bool:
            cognito = self._ensure_cognito()
            cognito.access_token = access_token
            return bool(cognito.verify_software_token(code.strip(), device_name))

        async with self._lock:
            try:
                verified = await asyncio.to_thread(_verify)
            except botocore.exceptions.ClientError as err:
                code_name = err.response.get("Error", {}).get("Code", "")
                if code_name in (
                    "CodeMismatchException",
                    "EnableSoftwareTokenMFAException",
                ):
                    raise InvalidChallengeResponseError(_error_message(err)) from err
                raise EvnexAuthError(_error_message(err)) from err
        if not verified:
            raise InvalidChallengeResponseError("The code was not accepted")

    async def set_mfa_preference(
        self,
        *,
        totp: bool = False,
        sms: bool = False,
        preferred: str | None = None,
    ) -> None:
        """Enable, disable, or reprioritise MFA methods for the account.

        With both flags False, MFA is disabled entirely (where the user
        pool allows it). preferred is "SMS" or "SOFTWARE_TOKEN"; it may be
        omitted when only one method is enabled.
        """
        if preferred is None and (totp ^ sms):
            preferred = "SOFTWARE_TOKEN" if totp else "SMS"
        access_token = await self.get_access_token()

        def _set_preference() -> None:
            cognito = self._ensure_cognito()
            cognito.access_token = access_token
            cognito.set_user_mfa_preference(
                sms_mfa=sms, software_token_mfa=totp, preferred=preferred
            )

        async with self._lock:
            try:
                await asyncio.to_thread(_set_preference)
            except botocore.exceptions.ClientError as err:
                raise EvnexAuthError(_error_message(err)) from err

    async def change_password(self, current_password: str, new_password: str) -> None:
        """Change the password of the signed-in account.

        Requires a usable session.

        :raises InvalidCredentialsError: the current password was wrong
        :raises EvnexAuthError: the new password was rejected, or a rate
            limit was hit
        """
        # Resolve the access token before taking the lock: get_access_token
        # may itself acquire self._lock, so locking first would deadlock.
        access_token = await self.get_access_token()

        def _change() -> None:
            cognito = self._ensure_cognito()
            cognito.access_token = access_token
            cognito.change_password(current_password, new_password)

        async with self._lock:
            try:
                await asyncio.to_thread(_change)
            except botocore.exceptions.ClientError as err:
                code = err.response.get("Error", {}).get("Code", "")
                if code == "NotAuthorizedException":
                    raise InvalidCredentialsError(_error_message(err)) from err
                raise EvnexAuthError(_error_message(err)) from err

    async def start_password_reset(self, username: str) -> str:
        """Begin the forgot-password flow, sending a reset code to the user.

        Needs no session. Returns a human-readable description of where the
        code was delivered (e.g. a masked email address), or "" if the
        server did not report a destination. Complete the reset with
        confirm_password_reset().

        :raises EvnexAuthError: the request was rejected (e.g. a rate limit)
        """

        def _start() -> str:
            cognito = self._ensure_cognito()
            cognito.username = username
            # pycognito's initiate_forgot_password discards the boto3
            # response, so call forgot_password directly to read the
            # delivery details. This client has no secret, so no SECRET_HASH
            # is required.
            response = cognito.client.forgot_password(
                ClientId=self._config.EVNEX_COGNITO_CLIENT_ID,
                Username=username,
            )
            delivery = response.get("CodeDeliveryDetails") or {}
            return str(delivery.get("Destination") or "")

        async with self._lock:
            try:
                return await asyncio.to_thread(_start)
            except botocore.exceptions.ClientError as err:
                raise EvnexAuthError(_error_message(err)) from err

    async def confirm_password_reset(
        self, username: str, code: str, new_password: str
    ) -> None:
        """Complete the forgot-password flow with the emailed/texted code.

        :raises InvalidChallengeResponseError: the reset code was wrong
        :raises ChallengeExpiredError: the reset code expired
        :raises EvnexAuthError: the new password was rejected
        """

        def _confirm() -> None:
            cognito = self._ensure_cognito()
            cognito.username = username
            cognito.confirm_forgot_password(code.strip(), new_password)

        async with self._lock:
            try:
                await asyncio.to_thread(_confirm)
            except botocore.exceptions.ClientError as err:
                code_name = err.response.get("Error", {}).get("Code", "")
                if code_name == "CodeMismatchException":
                    raise InvalidChallengeResponseError(_error_message(err)) from err
                if code_name == "ExpiredCodeException":
                    raise ChallengeExpiredError(_error_message(err)) from err
                raise EvnexAuthError(_error_message(err)) from err

    def _ensure_cognito(self) -> Cognito:
        """Build the pycognito client on first use.

        Only call from a worker-thread closure with the lock held:
        construction performs blocking I/O, and the returned instance is
        mutable shared state.
        """
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
        """Persist a newly issued token set, then make it the current one.

        Ordering matters: on_token_update completes before the assignment
        that makes the tokens visible to other tasks (including
        get_access_token's lock-free fast path), so a token set can never be
        used for a request before the application has persisted it.

        The callback's failures are logged and swallowed on purpose: the
        tokens are valid regardless, and a broken store must not take API
        access down with it. The application can always re-read .tokens.
        """
        assert self._lock.locked(), "_store_tokens requires the lock"
        if self._on_token_update is not None:
            try:
                await self._on_token_update(tokens)
            except Exception:
                logger.exception("on_token_update callback failed")
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
