# Design: async, persistence-aware token lifecycle (#113)

Target: python-evnex 0.7.0. Companion adoption: ha-evnex 0.9.0 (hardbyte/ha-evnex#91).

## Goals

From the acceptance criteria in #113:

1. Resume with a refresh token alone — no password (or username) supplied or retained.
2. Every new token set is published to the application before dependent requests proceed, so persistence can be atomic.
3. All public network operations are async; nothing blocks the event loop, including client construction.
4. No pycognito types in the public API.
5. Concurrent calls perform at most one refresh; a 401 triggers at most one forced refresh and one resend.
6. Auth recovery is independent of transient-network retry policy.
7. Authentication is enforced in one central request path, not per-method decorators.

Non-goals: replacing pycognito internally (it stays as the SRP/Cognito engine, fully
encapsulated); changing any resource method signatures or Pydantic schemas.

## Public API

All in a new `evnex/auth.py`; `evnex/api.py` keeps the resource client.

```python
@dataclass(frozen=True, slots=True)
class TokenSet:
    access_token: str = field(repr=False)
    id_token: str | None = field(repr=False)
    refresh_token: str | None = field(repr=False)
    expires_at: datetime | None      # decoded once from the access token JWT

    def to_dict(self) -> dict[str, Any]: ...          # JSON-safe, for config-entry storage
    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TokenSet: ...
```

`repr=False` keeps tokens out of logs/tracebacks. `from_dict` tolerates missing
`expires_at` (re-derived from the JWT) so existing ha-evnex entry data loads directly.

```python
@dataclass(frozen=True, slots=True)
class AuthChallenge:
    name: str                        # "SOFTWARE_TOKEN_MFA", "SMS_MFA", forward-compatible
    session: str                     # opaque Cognito session (short-lived, ~3 minutes)
    username: str                    # required by Cognito to answer the challenge
    parameters: Mapping[str, str]    # ChallengeParameters (e.g. FRIENDLY_DEVICE_NAME)

    def to_dict(self) / from_dict(cls, data)         # JSON-safe for cross-process flows
```

`name` stays a plain `str` (not an enum): Cognito has challenge types beyond MFA
(`NEW_PASSWORD_REQUIRED`, device auth) and unknown names must be forwardable, not a
parse error. Module constants `CHALLENGE_SOFTWARE_TOKEN_MFA` / `CHALLENGE_SMS_MFA`
cover the common comparisons.

### Exceptions (`evnex/errors.py`)

```python
class EvnexAuthError(NotAuthorizedException): ...        # base; see compat note
class InvalidCredentialsError(EvnexAuthError): ...       # bad username/password
class ReauthenticationRequiredError(EvnexAuthError): ... # refresh impossible; run interactive auth
class ChallengeExpiredError(EvnexAuthError): ...         # challenge session lapsed; restart
class InvalidChallengeResponseError(EvnexAuthError): ... # wrong code; same challenge may be retried
```

Mapping from Cognito error codes (encapsulated in one `_map_client_error` helper):

| Cognito code (context)                            | Raised as                        |
|---------------------------------------------------|----------------------------------|
| `NotAuthorizedException` (password auth)          | `InvalidCredentialsError`        |
| `NotAuthorizedException` / `ExpiredCodeException` (challenge) | `ChallengeExpiredError` |
| `CodeMismatchException` (challenge)               | `InvalidChallengeResponseError`  |
| any (refresh)                                     | `ReauthenticationRequiredError`  |

Compat: the base inherits from the existing `NotAuthorizedException`, so every
`except NotAuthorizedException` in current consumers keeps working through 0.7.x.
The pycognito challenge exceptions no longer escape any public call.

### EvnexAuth

```python
TokenUpdateCallback = Callable[[TokenSet], Awaitable[None]]

class EvnexAuth:
    def __init__(
        self,
        *,
        tokens: TokenSet | None = None,
        on_token_update: TokenUpdateCallback | None = None,
        config: EvnexConfig | None = None,
    ): ...

    async def start_authentication(
        self, username: str, password: str
    ) -> TokenSet | AuthChallenge: ...

    async def respond_to_challenge(
        self, challenge: AuthChallenge, response: str
    ) -> TokenSet | AuthChallenge: ...

    async def get_access_token(self) -> str: ...
    async def force_refresh(self, *, stale_access_token: str | None = None) -> TokenSet: ...

    @property
    def tokens(self) -> TokenSet | None: ...
```

Behavioural contract:

- **No credentials retained.** `username`/`password` exist only as arguments to
  `start_authentication`. Resumption needs only `TokenSet(refresh_token=...)`.
- **Lazy Cognito.** The pycognito/boto3 client is created on first use *inside*
  `asyncio.to_thread` (boto3 client construction performs blocking credential
  lookups — observed live via HA's blocking-call detector). Constructing
  `EvnexAuth` or `Evnex` on the event loop is always safe.
- **`get_access_token`** returns the current token, refreshing first only
  when `expires_at` says it is (nearly) expired — a stored-datetime comparison
  with ~30 s skew, no per-call JWT decode. This is the only proactive element;
  the 401 path remains the authority.
- **`force_refresh`** is single-flight: one `asyncio.Lock`; a caller that
  waited on the lock re-checks whether the tokens already rotated past its
  `stale_access_token` and returns without a second refresh. If there is no
  refresh token, or Cognito rejects it, raises `ReauthenticationRequiredError`.
- **`on_token_update`** is awaited *under the lock, before the new tokens are
  used by any request*, so the application persists atomically. Callback
  exceptions are logged and swallowed (`tokens` remains readable for
  reconciliation); a failing store must not take charging control offline.
- **Challenge chaining**: both interactive methods return `TokenSet |
  AuthChallenge` so a future second factor (e.g. `NEW_PASSWORD_REQUIRED`
  followed by MFA) is a loop for the caller, not an API change.

### Transport integration (`httpx.Auth`)

The per-method decorators and the 401 handling in `_check_api_response` are
replaced by one `httpx.Auth` implementation:

```python
class EvnexHttpxAuth(httpx.Auth):
    def __init__(self, auth: EvnexAuth): ...

    async def async_auth_flow(self, request):
        token = await self._auth.async_get_access_token()
        request.headers["Authorization"] = token
        response = yield request
        if response.status_code == 401:
            # 401 = rejected before execution; a single resend is safe
            # even for command endpoints.
            await self._auth.force_refresh(stale_access_token=token)
            request.headers["Authorization"] = await self._auth.get_access_token()
            yield request                      # exactly one retry
```

`Evnex` passes `auth=self._httpx_auth` on every request (auth is per-request in
httpx, so shared/injected `AsyncClient`s are unaffected). A second 401 after the
retry surfaces from response checking as `ReauthenticationRequiredError`, which
is in the non-retryable set — so tenacity's transient retry budget never
interacts with auth recovery (criterion 6), and no endpoint can forget auth
(criterion 7). `TokenRefreshedError` and the `ensure_authenticated` decorator
are deleted.

If there are no tokens at all and no refresh token, `get_access_token`
raises `ReauthenticationRequiredError` immediately — the application owns
interactive authentication (in ha-evnex this is `ConfigEntryAuthFailed` → the
reauth flow, which is already the shipped behaviour for MFA accounts).

### `Evnex` client

```python
class Evnex:
    def __init__(
        self,
        *,
        auth: EvnexAuth,
        httpx_client: AsyncClient | None = None,
        config: EvnexConfig | None = None,
    ): ...
```

- One `_request()` helper wraps `httpx_client.request(..., auth=...)` +
  response validation; resource methods become thin and decorator-free.
- The old constructor, sync auth methods, and token properties are removed
  (clean break, resolved decision 2). Token state is read via `evnex.auth.tokens`.

## ha-evnex adoption sketch (0.9.0, tracked in #91)

- Config entry stores `TokenSet.to_dict()` under one key (entry migration 1.4).
- `EvnexAuth(tokens=..., on_token_update=persist_to_entry)` replaces the
  poll-time token diffing; the stored password can then be dropped from entry
  data — reauth already collects it interactively.
- Config flow: `start_authentication()` returns `AuthChallenge` → show MFA form;
  `ChallengeExpiredError` → transparently restart the challenge (today this is
  inferred from a generic error); `InvalidChallengeResponseError` → "wrong code"
  without restarting.
- Coordinator: `ReauthenticationRequiredError` → `ConfigEntryAuthFailed`.

## Testing

Extends the 0.6.x suite (FakeCognito + respx + blockbuster):

- refresh-token-only startup; no password anywhere.
- `on_token_update` ordering: persisted before the triggering request resumes;
  callback failure doesn't break requests.
- Concurrency: N parallel first requests → one auth; N parallel 401s → one
  refresh (stale-token check).
- 401 → exactly one refresh + one resend (respx call counts); second 401 →
  `ReauthenticationRequiredError`, not retried by tenacity.
- Challenge lifecycle: TOTP happy path, wrong code (`InvalidChallengeResponseError`),
  expired session (`ChallengeExpiredError`), serialization round-trip of
  `AuthChallenge` (cross-process flows).
- blockbuster: `EvnexAuth()` and `Evnex()` construction plus every public call
  on the event loop, with the fake Cognito's simulated blocking I/O.
- One live validation pass against the real API before release (needs an MFA
  code — same procedure as the 0.6.0 verification).

## Resolved decisions (2026-07-17)

1. **Exceptions subclass `NotAuthorizedException`** — existing `except`
   clauses keep working; inheritance dropped in 0.8.0.
2. **Clean break on the constructor**: `Evnex(username, password, ...)`,
   the sync `authenticate()`/`respond_to_mfa_challenge()` methods, and the
   token properties are all removed in 0.7.0. `Evnex(auth=...)` is the only
   construction path; token state lives on `auth.tokens`.
3. **Bare method names**: `get_access_token()`, `force_refresh()`.
4. Client-side signature verification of resumed tokens is dropped: the
   server is the authority, and a bad token simply 401s into the refresh
   path. (pycognito still verifies tokens it *issues* in `_set_tokens`.)
