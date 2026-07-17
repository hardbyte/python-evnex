import logging
from importlib.metadata import PackageNotFoundError, version
from warnings import warn

import pydantic
from httpx import AsyncClient, HTTPStatusError, ReadTimeout, Response
from pydantic import ValidationError
from pydantic_core import from_json
from tenacity import (
    retry,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from evnex.auth import EvnexAuth, EvnexHttpxAuth
from evnex.config import EvnexConfig
from evnex.errors import NotAuthorizedException, ReauthenticationRequiredError
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


NON_RETRYABLE_EXCEPTIONS = (
    ValidationError,
    NotAuthorizedException,
)


def api_retry(*extra_non_retryable: type[BaseException]):
    """Retry transient API failures with backoff.

    Authentication failures and validation errors are never retried, nor are
    any exception types passed as arguments. Authentication recovery happens
    in the transport layer (EvnexHttpxAuth), independent of this policy.
    """
    return retry(
        wait=wait_random_exponential(multiplier=1, max=60),
        stop=stop_after_attempt(5),
        retry=retry_if_not_exception_type(
            NON_RETRYABLE_EXCEPTIONS + extra_non_retryable
        ),
    )


class Evnex:
    def __init__(
        self,
        *,
        auth: EvnexAuth,
        httpx_client: AsyncClient | None = None,
        config: EvnexConfig | None = None,
    ):
        """
        Create an Evnex API client.

        :param auth: the authentication component owning the session tokens
        :param httpx_client: optionally share an httpx AsyncClient
        :param config: override API endpoints or the default org
        """
        logger.debug("Creating evnex api instance")
        self.httpx_client = httpx_client or AsyncClient()
        if config is None:
            config = EvnexConfig()
        self.auth = auth
        self.org_id = config.EVNEX_ORG_ID
        self.version = EVNEX_VERSION
        self._httpx_auth = EvnexHttpxAuth(auth)

    @property
    def _common_headers(self):
        # Authorization is injected per-request by EvnexHttpxAuth
        return {
            "Accept": "application/json",
            "content-type": "application/json",
            "User-Agent": f"python-evnex/{self.version}",
        }

    async def _request(self, method: str, url: str, **kwargs) -> Response:
        """Single request path: header injection, auth, and 401 recovery."""
        return await self.httpx_client.request(
            method,
            url,
            headers=self._common_headers,
            auth=self._httpx_auth,
            **kwargs,
        )

    @api_retry()
    async def get_user_detail(self) -> EvnexUserDetail:
        response = await self._request(
            "GET",
            "https://client-api.evnex.io/v2/apps/user",
        )
        response_json = await self._check_api_response(response)
        data = EvnexGetUserResponse.model_validate(response_json).data

        # Make the assumption that most end users are only in one org
        if len(data.organisations):
            self.org_id = data.organisations[0].id

        return data

    async def _check_api_response(self, response):
        if response.status_code == 401:
            # EvnexHttpxAuth already refreshed and re-sent once; a 401 here
            # means the renewed session is still rejected
            raise ReauthenticationRequiredError(
                "Request still unauthorized after refreshing the session"
            )
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
    async def get_org_charge_points(
        self, org_id: str | None = None
    ) -> list[EvnexChargePoint]:
        if org_id is None and self.org_id:
            org_id = self.org_id
        logger.debug("Listing org charge points")
        r = await self._request(
            "GET",
            f"https://client-api.evnex.io/v2/apps/organisations/{org_id}/charge-points",
        )
        json_data = await self._check_api_response(r)
        return EvnexGetChargePointsResponse.model_validate(json_data).data.items

    @api_retry()
    async def get_org_insight(
        self, days: int, org_id: str | None = None, tz_offset: int = 12
    ) -> list[EvnexOrgInsightEntry]:
        if org_id is None and self.org_id:
            org_id = self.org_id
        logger.debug("Getting org insight")
        r = await self._request(
            "GET",
            f"https://client-api.evnex.io/organisations/{org_id}/summary/insights",
            params={"days": days, "tz-offset": tz_offset},
        )
        json_data = await self._check_api_response(r)
        validated_data = EvnexGetOrgInsights.model_validate(json_data).data

        return [insight.attributes for insight in validated_data]

    @api_retry()
    async def get_org_summary_status(
        self, org_id: str | None = None
    ) -> EvnexOrgSummaryStatus:
        if org_id is None and self.org_id:
            org_id = self.org_id
        logger.debug("Getting org summary status")
        r = await self._request(
            "GET",
            f"https://client-api.evnex.io/v2/apps/organisations/{org_id}/summary/status",
        )
        json_data = await self._check_api_response(r)
        return EvnexGetOrgSummaryStatusResponse.model_validate(json_data).data

    @api_retry()
    async def get_charge_point_detail(
        self, charge_point_id: str
    ) -> EvnexChargePointDetail:
        warn(
            "This method is deprecated. See get_charge_point_detail_v3",
            DeprecationWarning,
            stacklevel=2,
        )
        r = await self._request(
            "GET",
            f"https://client-api.evnex.io/v2/apps/charge-points/{charge_point_id}",
        )
        json_data = await self._check_api_response(r)
        return EvnexGetChargePointDetailResponse.model_validate(json_data).data

    @api_retry(TypeError)
    async def get_charge_point_detail_v3(
        self, charge_point_id: str
    ) -> EvnexV3APIResponse[EvnexChargePointDetailV3]:
        r = await self._request(
            "GET",
            f"https://client-api.evnex.io/charge-points/{charge_point_id}",
        )
        logger.debug(
            f"Raw get charge point detail response.\n{r.status_code}\n{r.text}"
        )
        json_data = await self._check_api_response(r)

        return EvnexV3APIResponse[EvnexChargePointDetailV3].model_validate(json_data)

    @api_retry(ReadTimeout)
    async def get_charge_point_solar_config(
        self, charge_point_id: str
    ) -> EvnexChargePointSolarConfig:
        """
        :param charge_point_id:
        :raises: ReadTimeout if the charge point is offline.
        """
        r = await self._request(
            "POST",
            f"https://client-api.evnex.io/charge-points/{charge_point_id}/commands/get-solar",
        )
        json_data = await self._check_api_response(r)

        return EvnexChargePointSolarConfig.model_validate(json_data)

    @api_retry(ReadTimeout)
    async def get_charge_point_override(
        self, charge_point_id: str
    ) -> EvnexChargePointOverrideConfig:
        """

        :param charge_point_id:
        :raises: ReadTimeout if the charge point is offline.
        """
        r = await self._request(
            "POST",
            f"https://client-api.evnex.io/charge-points/{charge_point_id}/commands/get-override",
            timeout=15,
        )
        json_data = await self._check_api_response(r)
        return EvnexChargePointOverrideConfig.model_validate(json_data)

    @api_retry()
    async def set_charge_point_override(
        self, charge_point_id: str, charge_now: bool, connector_id: int = 1
    ):
        r = await self._request(
            "POST",
            f"https://client-api.evnex.io/charge-points/{charge_point_id}/commands/set-override",
            json={"connectorId": connector_id, "chargeNow": charge_now},
        )
        r.raise_for_status()
        return True

    @api_retry(ReadTimeout)
    async def get_charge_point_status(
        self, charge_point_id: str
    ) -> EvnexChargePointStatusResponse:
        """
        :param charge_point_id:
        :raises: ReadTimeout if the charge point is offline.
        """
        r = await self._request(
            "POST",
            f"https://client-api.evnex.io/charge-points/{charge_point_id}/commands/get-status",
        )
        json_data = await self._check_api_response(r)

        return EvnexChargePointStatusResponse.model_validate(json_data)

    @api_retry(ReadTimeout)
    async def get_charge_point_energy_meter_reading(
        self, charge_point_id: str
    ) -> EvnexChargePointEnergyMeterReadingResponse:
        """
        :param charge_point_id:
        :raises: ReadTimeout if the charge point is offline.
        """
        r = await self._request(
            "POST",
            f"https://client-api.evnex.io/charge-points/{charge_point_id}/commands/get-energy-meter-reading",
        )
        json_data = await self._check_api_response(r)

        return EvnexChargePointEnergyMeterReadingResponse.model_validate(json_data)

    @api_retry()
    async def get_charge_point_transactions(
        self, charge_point_id: str
    ) -> list[EvnexChargePointTransaction]:
        warn(
            "This method is deprecated. See get_charge_point_sessions",
            DeprecationWarning,
            stacklevel=2,
        )
        # Similar to f'https://client-api.evnex.io/v3/charge-points/{charge_point_id}/sessions',

        r = await self._request(
            "GET",
            f"https://client-api.evnex.io/v2/apps/charge-points/{charge_point_id}/transactions",
        )
        json_data = await self._check_api_response(r)
        return EvnexGetChargePointTransactionsResponse.model_validate(
            json_data
        ).data.items

    @api_retry()
    async def get_charge_point_sessions(
        self, charge_point_id: str
    ) -> list[EvnexChargePointSession]:
        r = await self._request(
            "GET",
            f"https://client-api.evnex.io/charge-points/{charge_point_id}/sessions",
        )
        json_data = await self._check_api_response(r)
        return EvnexGetChargePointSessionsResponse.model_validate(json_data).data

    @api_retry(ReadTimeout)
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
        r = await self._request(
            "POST",
            f"https://client-api.evnex.io/v2/apps/organisations/{org_id}/charge-points/{charge_point_id}/commands/remote-stop-transaction",
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
        r = await self._request(
            "POST",
            f"https://client-api.evnex.io/v2/apps/organisations/{org_id}/charge-points/{charge_point_id}/commands/change-availability",
            json={"connectorId": connector_id, "changeAvailabilityType": availability},
            timeout=timeout,
        )
        json_data = await self._check_api_response(r)

        return EvnexCommandResponseV3.model_validate(json_data["data"])

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
        r = await self._request(
            "POST",
            f"https://client-api.evnex.io/v2/apps/organisations/{self.org_id}/charge-points/{charge_point_id}/commands/unlock-connector",
            json={"connectorId": connector_id, "changeAvailabilityType": availability},
            timeout=timeout,
        )
        json_data = await self._check_api_response(r)
        return EvnexCommandResponse.model_validate(json_data["data"])

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

        r = await self._request(
            "PUT",
            f"https://client-api.evnex.io/v2/apps/charge-points/{charge_point_id}/load-management",
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

        r = await self._request(
            "PUT",
            f"https://client-api.evnex.io/v2/apps/charge-points/{charge_point_id}/charge-schedule",
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
