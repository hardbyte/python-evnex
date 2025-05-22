import logging
from importlib.metadata import PackageNotFoundError, version
from typing import Optional
from warnings import warn

import botocore
import pydantic
from pydantic_core import from_json
from httpx import AsyncClient, ReadTimeout
from pycognito import Cognito
from pydantic import HttpUrl, ValidationError
from tenacity import retry, retry_if_not_exception_type, wait_random_exponential

from evnex.errors import NotAuthorizedException
from evnex.schema.charge_points import (
    EvnexChargePoint,
    EvnexChargePointDetail,
    EvnexChargePointLoadSchedule,
    EvnexChargePointOverrideConfig,
    EvnexChargePointSolarConfig,
    EvnexChargeProfileSegment,
    EvnexChargePointTransaction,
    EvnexGetChargePointTransactionsResponse,
    EvnexGetChargePointDetailResponse,
    EvnexGetChargePointsResponse,
)
from evnex.schema.commands import EvnexCommandResponse
from evnex.schema.org import (
    EvnexOrgInsightEntry,
    EvnexGetOrgSummaryStatusResponse,
    EvnexGetOrgInsights,
)
from evnex.schema.user import EvnexGetUserResponse, EvnexUserDetail
from evnex.schema.v3.charge_points import (
    EvnexChargePointDetail as EvnexChargePointDetailV3,
    EvnexGetChargePointSessionsResponse,
    EvnexChargePointSession,
)
from evnex.schema.v3.commands import EvnexCommandResponse as EvnexCommandResponseV3
from evnex.schema.v3.generic import EvnexV3APIResponse
from pydantic_settings import BaseSettings

from schema.org import EvnexOrgSummaryStatus

logger = logging.getLogger("evnex.api")


class EvnexConfig(BaseSettings):
    EVNEX_BASE_URL: HttpUrl = "https://client-api.evnex.io"
    EVNEX_COGNITO_USER_POOL_ID: str = "ap-southeast-2_zWnqo6ASv"
    EVNEX_COGNITO_CLIENT_ID: str = "rol3lsv2vg41783550i18r7vi"
    EVNEX_ORG_ID: str | None = None


class Evnex:
    def __init__(
        self,
        username: str,
        password: str,
        id_token=None,
        refresh_token=None,
        access_token=None,
        config: EvnexConfig = None,
        httpx_client: AsyncClient = None,
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

        if any(token is None for token in {id_token, access_token, refresh_token}):
            logger.debug("Starting cognito auth flow")
            self.authenticate()

        try:
            self.version = version("evnex")
        except PackageNotFoundError:
            self.version = "unknown"

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
        Authenticate the user and update the access_token

        :raises NotAuthorizedException
        """
        logger.debug("Authenticating to EVNEX cloud api")
        try:
            self.cognito.authenticate(password=self.password)
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

    @retry(
        wait=wait_random_exponential(multiplier=1, max=60),
        retry=retry_if_not_exception_type((ValidationError, NotAuthorizedException)),
    )
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
            logger.debug("Access Token likely expired, re-authenticate then retry")
            raise NotAuthorizedException()
        if not response.is_success:
            logger.warning(
                f"Unsuccessful request\n{response.status_code}\n{response.text}"
            )
        logger.debug(
            f"Raw EVNEX API response.\n{response.status_code}\n{response.text}"
        )

        response.raise_for_status()

        try:
            return from_json(response.text)
        except:
            logger.debug(
                f"Invalid json response.\n{response.status_code}\n{response.text}"
            )
            raise

    @retry(
        wait=wait_random_exponential(multiplier=1, max=60),
        retry=retry_if_not_exception_type((ValidationError, NotAuthorizedException)),
    )
    async def get_org_charge_points(
        self, org_id: Optional[str] = None
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

    @retry(
        wait=wait_random_exponential(multiplier=1, max=60),
        retry=retry_if_not_exception_type((ValidationError, NotAuthorizedException)),
    )
    async def get_org_insight(
        self, days: int, org_id: Optional[str] = None, tz_offset: int = 12
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

    @retry(
        wait=wait_random_exponential(multiplier=1, max=60),
        retry=retry_if_not_exception_type((ValidationError, NotAuthorizedException)),
    )
    async def get_org_summary_status(
        self, org_id: Optional[str] = None
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

    @retry(
        wait=wait_random_exponential(multiplier=1, max=60),
        retry=retry_if_not_exception_type((ValidationError, NotAuthorizedException)),
    )
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

    @retry(
        wait=wait_random_exponential(multiplier=1, max=60),
        retry=retry_if_not_exception_type(
            (TypeError, ValidationError, NotAuthorizedException)
        ),
    )
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

    @retry(
        wait=wait_random_exponential(multiplier=1, max=60),
        retry=retry_if_not_exception_type((ValidationError, NotAuthorizedException)),
    )
    async def get_charge_point_solar_config(
        self, charge_point_id: str
    ) -> EvnexChargePointSolarConfig:
        """
        :param charge_point_id:
        :raises: ReadTimeout if the charge point is offline.
        """
        r = await self.httpx_client.post(
            f"https://client-api.evnex.io/v3/charge-points/{charge_point_id}/commands/get-solar",
            headers=self._common_headers,
        )
        json_data = await self._check_api_response(r)

        return EvnexChargePointSolarConfig.model_validate(json_data)

    @retry(
        wait=wait_random_exponential(multiplier=1, max=60),
        retry=retry_if_not_exception_type(
            (ValidationError, NotAuthorizedException, ReadTimeout)
        ),
    )
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

    @retry(
        wait=wait_random_exponential(multiplier=1, max=60),
        retry=retry_if_not_exception_type((ValidationError, NotAuthorizedException)),
    )
    async def set_charge_point_override(
        self, charge_point_id: str, charge_now: bool, connector_id: int = 1
    ):
        r = await self.httpx_client.post(
            f"https://client-api.evnex.io/v3/charge-points/{charge_point_id}/commands/set-override",
            headers=self._common_headers,
            json={"connectorId": connector_id, "chargeNow": charge_now},
        )
        r.raise_for_status()
        return True

    @retry(
        wait=wait_random_exponential(multiplier=1, max=60),
        retry=retry_if_not_exception_type((ValidationError, NotAuthorizedException)),
    )
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

    @retry(
        wait=wait_random_exponential(multiplier=1, max=60),
        retry=retry_if_not_exception_type((ValidationError, NotAuthorizedException)),
    )
    async def get_charge_point_sessions(
        self, charge_point_id: str
    ) -> list[EvnexChargePointSession]:
        r = await self.httpx_client.get(
            f"https://client-api.evnex.io/charge-points/{charge_point_id}/sessions",
            headers=self._common_headers,
        )
        json_data = await self._check_api_response(r)
        return EvnexGetChargePointSessionsResponse.model_validate(json_data).data

    @retry(
        wait=wait_random_exponential(multiplier=1, max=60),
        retry=retry_if_not_exception_type(
            (ValidationError, NotAuthorizedException, ReadTimeout)
        ),
    )
    async def stop_charge_point(
        self,
        charge_point_id: str,
        org_id: Optional[str] = None,
        connector_id: str = "1",
        timeout=10,
    ) -> EvnexCommandResponse:
        """
        Stop an active charging session.

        Note the vehicle will need to be unplugged before starting a new session.

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

    async def enable_charger(self, charge_point_id: str, connector_id: int | str = 1):
        await self.set_charger_availability(
            charge_point_id=charge_point_id, available=True, connector_id=connector_id
        )

    async def disable_charger(self, charge_point_id: str, connector_id: int | str = 1):
        await self.set_charger_availability(
            charge_point_id=charge_point_id, available=False, connector_id=connector_id
        )

    async def set_charger_availability(
        self,
        charge_point_id: str,
        available: bool = True,
        connector_id: int | str = 1,
        timeout=10,
    ) -> EvnexCommandResponse:
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
            f"https://client-api.evnex.io/v3/charge-points/{charge_point_id}/commands/change-availability",
            headers=self._common_headers,
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
        r = await self.httpx_client.post(
            f"https://client-api.evnex.io/v2/apps/organisations/{self.org_id}/charge-points/{charge_point_id}/commands/unlock-connector",
            headers=self._common_headers,
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
