import asyncio
import logging
from functools import wraps
from importlib.metadata import PackageNotFoundError, version
from typing import Literal
from warnings import warn

import botocore
import pydantic
from httpx import AsyncClient, HTTPStatusError, ReadTimeout
from pycognito import Cognito
from pycognito.exceptions import (
    MFAChallengeException,
    TokenVerificationException,
)
from pydantic import ValidationError
from pydantic_core import from_json
from pydantic_settings import BaseSettings
from tenacity import (
    retry,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from evnex.errors import NotAuthorizedException, TokenRefreshedError
from evnex.schema.charge_points import (
    EvnexChargePoint,
    EvnexChargePointDetail,
    EvnexChargePointEnergyMeterReadingResponse,
    EvnexChargePointLoadSchedule,
    EvnexChargePointOverrideConfig,
    EvnexChargePointSolarConfig,
    EvnexChargePointStatusResponse,
    EvnexChargePointTransaction,
    EvnexChargeProfileSegment,
    EvnexGetChargePointDetailResponse,
    EvnexGetChargePointsResponse,
    EvnexGetChargePointTransactionsResponse,
)
from evnex.schema.commands import EvnexCommandResponse
from evnex.schema.org import (
    EvnexGetOrgInsights,
    EvnexGetOrgSummaryStatusResponse,
    EvnexOrgInsightEntry,
    EvnexOrgSummaryStatus,
)
from evnex.schema.user import EvnexGetUserResponse, EvnexUserDetail
from evnex.schema.v3.charge_points import (
    EvnexChargePointDetail as EvnexChargePointDetailV3,
)
from evnex.schema.v3.charge_points import (
    EvnexChargePointSession,
    EvnexGetChargePointSessionsResponse,
)
from evnex.schema.v3.commands import EvnexCommandResponse as EvnexCommandResponseV3
from evnex.schema.v3.generic import EvnexV3APIResponse

logger = logging.getLogger("evnex.api")

try:
    EVNEX_VERSION = version("evnex")
except PackageNotFoundError:
    EVNEX_VERSION = "unknown"


class EvnexConfig(BaseSettings):
    EVNEX_BASE_URL: str = "https://client-api.evnex.io"
    EVNEX_COGNITO_USER_POOL_ID: str = "ap-southeast-2_zWnqo6ASv"
    EVNEX_COGNITO_CLIENT_ID: str = "rol3lsv2vg41783550i18r7vi"
    EVNEX_ORG_ID: str | None = None


NON_RETRYABLE_EXCEPTIONS = (
    ValidationError,
    NotAuthorizedException,
    MFAChallengeException,
)


def _raise_final_attempt(retry_state):
    """Called by tenacity once retries are exhausted."""
    exception = retry_state.outcome.exception()
    if isinstance(exception, TokenRefreshedError):
        raise NotAuthorizedException(
            "Request still unauthorized after refreshing tokens"
        ) from exception
    raise exception


def api_retry(*extra_non_retryable: type[BaseException]):
    """Retry transient API failures with backoff.

    Authentication failures, MFA challenges and validation errors are never
    retried, nor are any exception types passed as arguments.
    """
    return retry(
        wait=wait_random_exponential(multiplier=1, max=60),
        stop=stop_after_attempt(5),
        retry=retry_if_not_exception_type(
            NON_RETRYABLE_EXCEPTIONS + extra_non_retryable
        ),
        retry_error_callback=_raise_final_attempt,
    )


def refresh_token_if_expired(func):
    """Decorator to ensure the token is valid before making an API call."""

    @wraps(func)
    async def wrapper(self, *args, **kwargs):
        await self._ensure_valid_token()
        return await func(self, *args, **kwargs)

    return wrapper


class Evnex:
    def __init__(
        self,
        username: str,
        password: str,
        id_token=None,
        refresh_token=None,
        access_token=None,
        config: EvnexConfig | None = None,
        httpx_client: AsyncClient | None = None,
    ):
        """
        Create an Evnex API client.

        :param username:
        :param password:
        :param id_token: ID Token returned by authentication
        :param refresh_token: Refresh Token returned by authentication
        :param access_token: Access Token returned by authentication
        :param httpx_client:
        """
        self.httpx_client = httpx_client or AsyncClient()
        if config is None:
            config = EvnexConfig()
        logger.debug("Creating evnex api instance")
        self.username = username
        self.password = password

        self.org_id = config.EVNEX_ORG_ID

        self.cognito = Cognito(
            user_pool_id=config.EVNEX_COGNITO_USER_POOL_ID,
            client_id=config.EVNEX_COGNITO_CLIENT_ID,
            username=username,
            id_token=id_token,
            refresh_token=refresh_token,
            access_token=access_token,
        )

        self._token_lock = asyncio.Lock()
        # Tokens passed in by the caller haven't been through pycognito's
        # signature verification; defer that to the first API call rather
        # than doing network I/O in the constructor.
        self._resumed_tokens_verified = access_token is None

        self.version = EVNEX_VERSION

    async def _ensure_valid_token(self) -> bool:
        """Ensure we hold a valid access token, authenticating or refreshing as needed.

        Returns True if new tokens were obtained.

        :raises NotAuthorizedException: authentication or token refresh failed
        :raises SoftwareTokenMFAChallengeException: respond with a TOTP app code
        :raises SMSMFAChallengeException: respond with a code sent by SMS
        """
        async with self._token_lock:
            if self.cognito.access_token is None:
                if self.cognito.refresh_token:
                    logger.debug("No access token, renewing with refresh token")
                    try:
                        await asyncio.to_thread(self.cognito.renew_access_token)
                    except botocore.exceptions.ClientError as e:
                        raise NotAuthorizedException(str(e)) from e
                else:
                    logger.debug("No tokens, starting cognito auth flow")
                    await asyncio.to_thread(self.authenticate)
                self._resumed_tokens_verified = True
                return True

            logger.debug("Checking if JWT tokens need to be refreshed")
            try:
                # check_token() renews via the refresh token if expired
                renewed = bool(await asyncio.to_thread(self.cognito.check_token))
            except botocore.exceptions.ClientError as e:
                logger.error(f"Failed to refresh token: {e}")
                raise NotAuthorizedException(str(e)) from e

            if renewed:
                # pycognito verifies newly issued tokens in _set_tokens
                self._resumed_tokens_verified = True
            elif not self._resumed_tokens_verified:
                try:
                    await asyncio.to_thread(self.cognito.verify_tokens)
                except TokenVerificationException as e:
                    raise NotAuthorizedException(str(e)) from e
                self._resumed_tokens_verified = True
            return renewed

    @property
    def _common_headers(self):
        return {
            "Accept": "application/json",
            "content-type": "application/json",
            "Authorization": self.access_token,
            "User-Agent": f"python-evnex/{self.version}",
        }

    def authenticate(self):
        """
        Authenticate the user and update the access_token.

        Note this isn't usually required: API methods authenticate on first
        use. Call it directly when the account may have multifactor
        authentication enabled, catch the challenge exception, obtain a code
        from the user, then call respond_to_mfa_challenge().

        :raises NotAuthorizedException: authentication failed
        :raises SoftwareTokenMFAChallengeException: respond with a TOTP app code
        :raises SMSMFAChallengeException: respond with a code sent by SMS
        """
        logger.debug("Authenticating to EVNEX cloud api")
        try:
            self.cognito.authenticate(password=self.password)
        except MFAChallengeException:
            raise
        except botocore.exceptions.ClientError as e:
            raise NotAuthorizedException(e.args[0]) from e

    def respond_to_mfa_challenge(
        self,
        mfa_code: str,
        mode: Literal["SMS", "TOTP"],
        mfa_tokens=None,
    ):
        """
        Respond to a multifactor authentication challenge either via SMS or TOTP app.

        The challenge session is kept on this instance by authenticate(); to
        respond from a different instance or process, pass mfa_tokens from
        the challenge exception's get_tokens(). Note Cognito challenge
        sessions are short-lived (around 3 minutes).

        :raises NotAuthorizedException: the code was rejected or the challenge expired
        :raises ValueError: mode is not "SMS" or "TOTP"
        """
        logger.debug("MFA Challenge and response issued.")

        try:
            match mode:
                case "SMS":
                    self.cognito.respond_to_sms_mfa_challenge(
                        mfa_code, mfa_tokens=mfa_tokens
                    )
                case "TOTP":
                    self.cognito.respond_to_software_token_mfa_challenge(
                        mfa_code, mfa_tokens=mfa_tokens
                    )
                case _:
                    raise ValueError(
                        f"Unknown MFA mode {mode!r}, expected 'SMS' or 'TOTP'"
                    )
        except botocore.exceptions.ClientError as e:
            raise NotAuthorizedException(e.args[0]) from e

    @property
    def access_token(self):
        return self.cognito.access_token

    @property
    def id_token(self):
        return self.cognito.id_token

    @property
    def refresh_token(self):
        return self.cognito.refresh_token

    @api_retry()
    @refresh_token_if_expired
    async def get_user_detail(self) -> EvnexUserDetail:
        response = await self.httpx_client.get(
            "https://client-api.evnex.io/v2/apps/user", headers=self._common_headers
        )
        response_json = await self._check_api_response(response)
        data = EvnexGetUserResponse.model_validate(response_json).data

        # Make the assumption that most end users are only in one org
        if len(data.organisations):
            self.org_id = data.organisations[0].id

        return data

    async def _check_api_response(self, response):
        if response.status_code == 401:
            logger.debug("Got a 401, attempting to refresh tokens")
            if await self._ensure_valid_token():
                # New tokens were obtained: the retry policy re-sends the
                # request with them. A persistent 401 surfaces to the caller
                # as NotAuthorizedException via _raise_final_attempt.
                raise TokenRefreshedError()
            raise NotAuthorizedException()
        if not response.is_success:
            logger.warning(
                f"Unsuccessful request\n{response.status_code}\n{response.text}"
            )
        # logger.debug(
        #     f"Raw EVNEX API response.\n{response.status_code}\n{response.text}"
        # )

        response.raise_for_status()

        try:
            return from_json(response.text)
        except:
            logger.debug(
                f"Invalid json response.\n{response.status_code}\n{response.text}"
            )
            raise

    @api_retry(HTTPStatusError)
    @refresh_token_if_expired
    async def get_org_charge_points(
        self, org_id: str | None = None
    ) -> list[EvnexChargePoint]:
        if org_id is None and self.org_id:
            org_id = self.org_id
        logger.debug("Listing org charge points")
        r = await self.httpx_client.get(
            f"https://client-api.evnex.io/v2/apps/organisations/{org_id}/charge-points",
            headers=self._common_headers,
        )
        json_data = await self._check_api_response(r)
        return EvnexGetChargePointsResponse.model_validate(json_data).data.items

    @api_retry()
    @refresh_token_if_expired
    async def get_org_insight(
        self, days: int, org_id: str | None = None, tz_offset: int = 12
    ) -> list[EvnexOrgInsightEntry]:
        if org_id is None and self.org_id:
            org_id = self.org_id
        logger.debug("Getting org insight")
        r = await self.httpx_client.get(
            f"https://client-api.evnex.io/organisations/{org_id}/summary/insights",
            headers=self._common_headers,
            params={"days": days, "tz-offset": tz_offset},
        )
        json_data = await self._check_api_response(r)
        validated_data = EvnexGetOrgInsights.model_validate(json_data).data

        return [insight.attributes for insight in validated_data]

    @api_retry()
    @refresh_token_if_expired
    async def get_org_summary_status(
        self, org_id: str | None = None
    ) -> EvnexOrgSummaryStatus:
        if org_id is None and self.org_id:
            org_id = self.org_id
        logger.debug("Getting org summary status")
        r = await self.httpx_client.get(
            f"https://client-api.evnex.io/v2/apps/organisations/{org_id}/summary/status",
            headers=self._common_headers,
        )
        json_data = await self._check_api_response(r)
        return EvnexGetOrgSummaryStatusResponse.model_validate(json_data).data

    @api_retry()
    @refresh_token_if_expired
    async def get_charge_point_detail(
        self, charge_point_id: str
    ) -> EvnexChargePointDetail:
        warn(
            "This method is deprecated. See get_charge_point_detail_v3",
            DeprecationWarning,
            stacklevel=2,
        )
        r = await self.httpx_client.get(
            f"https://client-api.evnex.io/v2/apps/charge-points/{charge_point_id}",
            headers=self._common_headers,
        )
        json_data = await self._check_api_response(r)
        return EvnexGetChargePointDetailResponse.model_validate(json_data).data

    @api_retry(TypeError)
    @refresh_token_if_expired
    async def get_charge_point_detail_v3(
        self, charge_point_id: str
    ) -> EvnexV3APIResponse[EvnexChargePointDetailV3]:
        r = await self.httpx_client.get(
            f"https://client-api.evnex.io/charge-points/{charge_point_id}",
            headers=self._common_headers,
        )
        logger.debug(
            f"Raw get charge point detail response.\n{r.status_code}\n{r.text}"
        )
        json_data = await self._check_api_response(r)

        return EvnexV3APIResponse[EvnexChargePointDetailV3].model_validate(json_data)

    @api_retry(ReadTimeout)
    @refresh_token_if_expired
    async def get_charge_point_solar_config(
        self, charge_point_id: str
    ) -> EvnexChargePointSolarConfig:
        """
        :param charge_point_id:
        :raises: ReadTimeout if the charge point is offline.
        """
        r = await self.httpx_client.post(
            f"https://client-api.evnex.io/charge-points/{charge_point_id}/commands/get-solar",
            headers=self._common_headers,
        )
        json_data = await self._check_api_response(r)

        return EvnexChargePointSolarConfig.model_validate(json_data)

    @api_retry(ReadTimeout)
    @refresh_token_if_expired
    async def get_charge_point_override(
        self, charge_point_id: str
    ) -> EvnexChargePointOverrideConfig:
        """

        :param charge_point_id:
        :raises: ReadTimeout if the charge point is offline.
        """
        r = await self.httpx_client.post(
            f"https://client-api.evnex.io/charge-points/{charge_point_id}/commands/get-override",
            headers=self._common_headers,
            timeout=15,
        )
        json_data = await self._check_api_response(r)
        return EvnexChargePointOverrideConfig.model_validate(json_data)

    @api_retry()
    @refresh_token_if_expired
    async def set_charge_point_override(
        self, charge_point_id: str, charge_now: bool, connector_id: int = 1
    ):
        r = await self.httpx_client.post(
            f"https://client-api.evnex.io/charge-points/{charge_point_id}/commands/set-override",
            headers=self._common_headers,
            json={"connectorId": connector_id, "chargeNow": charge_now},
        )
        r.raise_for_status()
        return True

    @api_retry(ReadTimeout)
    @refresh_token_if_expired
    async def get_charge_point_status(
        self, charge_point_id: str
    ) -> EvnexChargePointStatusResponse:
        """
        :param charge_point_id:
        :raises: ReadTimeout if the charge point is offline.
        """
        r = await self.httpx_client.post(
            f"https://client-api.evnex.io/charge-points/{charge_point_id}/commands/get-status",
            headers=self._common_headers,
        )
        json_data = await self._check_api_response(r)

        return EvnexChargePointStatusResponse.model_validate(json_data)

    @api_retry(ReadTimeout)
    @refresh_token_if_expired
    async def get_charge_point_energy_meter_reading(
        self, charge_point_id: str
    ) -> EvnexChargePointEnergyMeterReadingResponse:
        """
        :param charge_point_id:
        :raises: ReadTimeout if the charge point is offline.
        """
        r = await self.httpx_client.post(
            f"https://client-api.evnex.io/charge-points/{charge_point_id}/commands/get-energy-meter-reading",
            headers=self._common_headers,
        )
        json_data = await self._check_api_response(r)

        return EvnexChargePointEnergyMeterReadingResponse.model_validate(json_data)

    @api_retry()
    @refresh_token_if_expired
    async def get_charge_point_transactions(
        self, charge_point_id: str
    ) -> list[EvnexChargePointTransaction]:
        warn(
            "This method is deprecated. See get_charge_point_sessions",
            DeprecationWarning,
            stacklevel=2,
        )
        # Similar to f'https://client-api.evnex.io/v3/charge-points/{charge_point_id}/sessions',

        r = await self.httpx_client.get(
            f"https://client-api.evnex.io/v2/apps/charge-points/{charge_point_id}/transactions",
            headers=self._common_headers,
        )
        json_data = await self._check_api_response(r)
        return EvnexGetChargePointTransactionsResponse.model_validate(
            json_data
        ).data.items

    @api_retry()
    @refresh_token_if_expired
    async def get_charge_point_sessions(
        self, charge_point_id: str
    ) -> list[EvnexChargePointSession]:
        r = await self.httpx_client.get(
            f"https://client-api.evnex.io/charge-points/{charge_point_id}/sessions",
            headers=self._common_headers,
        )
        json_data = await self._check_api_response(r)
        return EvnexGetChargePointSessionsResponse.model_validate(json_data).data

    @api_retry(ReadTimeout)
    @refresh_token_if_expired
    async def stop_charge_point(
        self,
        charge_point_id: str,
        org_id: str | None = None,
        connector_id: str = "1",
        timeout=10,
    ) -> EvnexCommandResponse:
        """
        Stop an active charging session.

        Note the vehicle will need to be unplugged before starting a new session.
        returns a 404 if the session is not active.

        :raises httpx.ReadTimeout error
            If there is no active charging session the server responds with a 504 Gateway Timeout,
            this manifests as a httpx.ReadTimeout error. This will be raised immediately without retry.

        """
        if org_id is None and self.org_id:
            org_id = self.org_id
        logger.info("Stopping charging session")
        r = await self.httpx_client.post(
            f"https://client-api.evnex.io/v2/apps/organisations/{org_id}/charge-points/{charge_point_id}/commands/remote-stop-transaction",
            headers=self._common_headers,
            # 'Connection': 'Keep-Alive'
            json={"connectorId": connector_id},
            timeout=timeout,
        )
        json_data = await self._check_api_response(r)

        return EvnexCommandResponse.model_validate(json_data["data"])

    async def enable_charger(
        self, org_id: str, charge_point_id: str, connector_id: int | str = 1
    ):
        await self.set_charger_availability(
            org_id=org_id,
            charge_point_id=charge_point_id,
            available=True,
            connector_id=connector_id,
        )

    async def disable_charger(
        self, org_id: str, charge_point_id: str, connector_id: int | str = 1
    ):
        await self.set_charger_availability(
            org_id=org_id,
            charge_point_id=charge_point_id,
            available=False,
            connector_id=connector_id,
        )

    @refresh_token_if_expired
    async def set_charger_availability(
        self,
        org_id: str,
        charge_point_id: str,
        available: bool = True,
        connector_id: int | str = 1,
        timeout=10,
    ) -> EvnexCommandResponseV3:
        """
        Change availability of charger.

        If the charger support multiple connectors, you can disable a specific connector.

        When a charge point is disabled the Charge Point Detail will include:
            ocppStatus: "UNAVAILABLE"
            ocppCode: "NoError"
        """
        availability = "Operative" if available else "Inoperative"
        logger.info(f"Changing connector {connector_id} to {availability}")
        r = await self.httpx_client.post(
            f"https://client-api.evnex.io/v2/apps/organisations/{org_id}/charge-points/{charge_point_id}/commands/change-availability",
            headers=self._common_headers,
            json={"connectorId": connector_id, "changeAvailabilityType": availability},
            timeout=timeout,
        )
        json_data = await self._check_api_response(r)

        return EvnexCommandResponseV3.model_validate(json_data["data"])

    @refresh_token_if_expired
    async def unlock_charger(
        self,
        charge_point_id: str,
        available: bool = True,
        connector_id: str = "0",
        timeout=10,
    ) -> EvnexCommandResponse:
        """
        Unlock charger.

        Only relevant for socketed chargers, this tells the charger to try to retract the pin which
        locks a cable in place (at the charger end) for the duration of a transaction. Some sockets
        have a sensor to tell them whether this has been successful or not, but some don’t, so they
        always report success when it might not have actually worked.

        Note this also serves to re-enable a disabled charger.
        """
        availability = "Operative" if available else "Inoperative"
        logger.info(f"Changing connector {connector_id} to {availability}")
        r = await self.httpx_client.post(
            f"https://client-api.evnex.io/v2/apps/organisations/{self.org_id}/charge-points/{charge_point_id}/commands/unlock-connector",
            headers=self._common_headers,
            json={"connectorId": connector_id, "changeAvailabilityType": availability},
            timeout=timeout,
        )
        json_data = await self._check_api_response(r)
        return EvnexCommandResponse.model_validate(json_data["data"])

    @refresh_token_if_expired
    async def set_charger_load_profile(
        self,
        charge_point_id: str,
        charging_profile_periods: list[EvnexChargeProfileSegment | dict[str, int]],
        enabled: bool = True,
        duration: int = 86400,
        units: str = "A",
        timeout=10,
    ) -> EvnexChargePointLoadSchedule:
        """
        Set a load management profile for the charger.

        Used to control the maximum output of a charge point.
        """
        logger.info("Applying load management profile")
        # Parse and validate the input
        schedule = [
            segment.dict()
            for segment in pydantic.parse_obj_as(
                list[EvnexChargeProfileSegment], charging_profile_periods
            )
        ]

        r = await self.httpx_client.put(
            f"https://client-api.evnex.io/v2/apps/charge-points/{charge_point_id}/load-management",
            headers=self._common_headers,
            json={
                "chargingProfilePeriods": schedule,
                "enabled": enabled,
                "units": units,
                "duration": duration,
            },
            timeout=timeout,
        )
        json_data = await self._check_api_response(r)
        return EvnexChargePointLoadSchedule.model_validate(json_data["data"])

    @refresh_token_if_expired
    async def set_charge_point_schedule(
        self,
        charge_point_id: str,
        charging_profile_periods: list[EvnexChargeProfileSegment | dict[str, int]],
        enabled: bool = True,
        duration: int = 86400,
        timeout=10,
    ) -> EvnexChargePointLoadSchedule:
        """
        Configure times that a charge point will charge between.
        Defaults to setting a daily period. Specify segments using seconds from midnight (using configured timezone).

        [
          {"start": 0, "limit": 0},
          {"start": 3600, "limit": 32},
          {"start": 4500, "limit": 0}
        ]
        """
        logger.info("Applying load management profile")
        # Parse and validate the input
        schedule = [
            segment.dict()
            for segment in pydantic.parse_obj_as(
                list[EvnexChargeProfileSegment], charging_profile_periods
            )
        ]

        r = await self.httpx_client.put(
            f"https://client-api.evnex.io/v2/apps/charge-points/{charge_point_id}/charge-schedule",
            headers=self._common_headers,
            json={
                "chargingProfilePeriods": schedule,
                "enabled": enabled,
                # "units": "A",
                "duration": duration,
                # "timezone": timezone,
            },
            timeout=timeout,
        )
        json_data = await self._check_api_response(r)
        return EvnexChargePointLoadSchedule.model_validate(json_data["data"])
