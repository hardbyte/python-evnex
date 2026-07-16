class NotAuthorizedException(ValueError):
    pass


class TokenRefreshedError(Exception):
    """A 401 response triggered a successful token refresh.

    Internal to the retry policy: the request is retried with the fresh
    tokens, and if it keeps failing the caller sees NotAuthorizedException.
    """
