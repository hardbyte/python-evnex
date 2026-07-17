"""Typed errors raised by the EVNEX client.

All authentication problems derive from EvnexAuthError; catch that to
handle "the session is not usable" generically, or a subclass to react to
a specific condition.
"""

import warnings


class EvnexAuthError(ValueError):
    """Base for authentication and session lifecycle errors."""


class InvalidCredentialsError(EvnexAuthError):
    """The username or password was rejected."""


class ReauthenticationRequiredError(EvnexAuthError):
    """The session cannot be renewed; interactive authentication is needed."""


class ChallengeExpiredError(EvnexAuthError):
    """The short-lived challenge session lapsed; restart authentication."""


class PasswordChangeRequiredError(EvnexAuthError):
    """A new password must be set before this account can sign in."""


class InvalidChallengeResponseError(EvnexAuthError):
    """The challenge response (e.g. MFA code) was rejected; retry is possible."""


def __getattr__(name: str):
    # Deprecated alias, served dynamically so importing it warns.
    # Removed in 0.8.0.
    if name == "NotAuthorizedException":
        warnings.warn(
            "NotAuthorizedException is deprecated and will be removed in "
            "evnex 0.8.0; catch EvnexAuthError instead",
            DeprecationWarning,
            stacklevel=2,
        )
        return EvnexAuthError
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
