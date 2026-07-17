class NotAuthorizedException(ValueError):
    """Historic base error for authentication problems.

    Retained so existing ``except NotAuthorizedException`` handlers keep
    working; new code should catch the EvnexAuthError hierarchy below.
    The inheritance will be removed in 0.8.0.
    """


class EvnexAuthError(NotAuthorizedException):
    """Base for authentication lifecycle errors."""


class InvalidCredentialsError(EvnexAuthError):
    """The username or password was rejected."""


class ReauthenticationRequiredError(EvnexAuthError):
    """The session cannot be renewed; interactive authentication is needed."""


class ChallengeExpiredError(EvnexAuthError):
    """The short-lived challenge session lapsed; restart authentication."""


class PasswordChangeRequiredError(EvnexAuthError):
    """Cognito requires a new password before this account can sign in."""


class InvalidChallengeResponseError(EvnexAuthError):
    """The challenge response (e.g. MFA code) was rejected; retry is possible."""
