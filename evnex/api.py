from importlib.metadata import PackageNotFoundError, version
from typing import Optional

import botocore
from httpx import AsyncClient, ReadTimeout
from pycognito import Cognito
from pydantic import BaseSettings, HttpUrl, ValidationError
from structlog import get_logger
from tenacity import retry, retry_if_not_exception_type, wait_random_exponential

from evnex.errors import NotAuthorizedException
from evnex.schema.charge_points import (
    EvnexChargePoint,
    EvnexChargePointDetail,
    EvnexChargePointOverrideConfig,
    EvnexChargePointSolarConfig,
    EvnexChargePointTransaction,
    EvnexGetChargePointDetailResponse,
    EvnexGetChargePointsResponse,
    EvnexGetChargePointTransactionsResponse,
)
from evnex.schema.commands import EvnexCommandResponse
from evnex.schema.org import EvnexGetOrgInsightResponse, EvnexOrgInsightEntry
from evnex.schema.user import EvnexGetUserResponse, EvnexUserDetail

logger = get_logger("evnex.api")


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

        self._common_headers = {
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
            self.cognito.authenticate(
                password=self.password
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

    @retry(
        wait=wait_random_exponential(multiplier=1, max=60),
        retry=retry_if_not_exception_type((ValidationError, NotAuthorizedException)),
    )
    async def get_user_detail(self) -> EvnexUserDetail:
        response = await self.httpx_client.get(
            "https://client-api.evnex.io/v2/apps/user", headers=self._common_headers
        )
        response_json = await self._check_api_response(response)
        data = EvnexGetUserResponse(**response_json).data

        # Make the assumption that most end users are only in one org
        if len(data.organisations):
            self.org_id = data.organisations[0].id

        return data

    async def _check_api_response(self, response):
        if response.status_code == 401:
            logger.debug("Access Token likely expired, re-authenticate then retry")
            raise NotAuthorizedException()

        response.raise_for_status()

        try:
            return response.json()
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
        return EvnexGetChargePointsResponse(**json_data).data.items

    @retry(
        wait=wait_random_exponential(multiplier=1, max=60),
        retry=retry_if_not_exception_type((ValidationError, NotAuthorizedException)),
    )
    async def get_org_insight(
        self, days: int, org_id: Optional[str] = None
    ) -> list[EvnexOrgInsightEntry]:
        if org_id is None and self.org_id:
            org_id = self.org_id
        logger.debug("Getting org insight", org_id=org_id)
        r = await self.httpx_client.get(
            f"https://client-api.evnex.io/v2/apps/organisations/{org_id}/summary/insights",
            headers=self._common_headers,
            params={"days": days},
        )
        json_data = await self._check_api_response(r)
        return EvnexGetOrgInsightResponse.parse_obj(json_data).data.items

    @retry(
        wait=wait_random_exponential(multiplier=1, max=60),
        retry=retry_if_not_exception_type((ValidationError, NotAuthorizedException)),
    )
    async def get_charge_point_detail(
        self, charge_point_id: str
    ) -> EvnexChargePointDetail:

        r = await self.httpx_client.get(
            f"https://client-api.evnex.io/v2/apps/charge-points/{charge_point_id}",
            headers=self._common_headers,
        )
        json_data = await self._check_api_response(r)
        return EvnexGetChargePointDetailResponse(**json_data).data

    @retry(
        wait=wait_random_exponential(multiplier=1, max=60),
        retry=retry_if_not_exception_type((ValidationError, NotAuthorizedException)),
    )
    async def get_charge_point_solar_config(
        self, charge_point_id: str
    ) -> EvnexChargePointSolarConfig:
        r = await self.httpx_client.post(
            f"https://client-api.evnex.io/v3/charge-points/{charge_point_id}/commands/get-solar",
            headers=self._common_headers,
        )
        json_data = await self._check_api_response(r)

        return EvnexChargePointSolarConfig(**json_data)

    @retry(
        wait=wait_random_exponential(multiplier=1, max=60),
        retry=retry_if_not_exception_type((ValidationError, NotAuthorizedException)),
    )
    async def get_charge_point_override(
        self, charge_point_id: str
    ) -> EvnexChargePointOverrideConfig:
        r = await self.httpx_client.post(
            f"https://client-api.evnex.io/v3/charge-points/{charge_point_id}/commands/get-override",
            headers=self._common_headers,
        )
        json_data = await self._check_api_response(r)
        return EvnexChargePointOverrideConfig(**json_data)

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
        # Similar to f'https://client-api.evnex.io/v3/charge-points/{charge_point_id}/sessions',

        r = await self.httpx_client.get(
            f"https://client-api.evnex.io/v2/apps/charge-points/{charge_point_id}/transactions",
            headers=self._common_headers,
        )
        json_data = await self._check_api_response(r)

        return EvnexGetChargePointTransactionsResponse(**json_data).data.items

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
        logger.info("Stopping charging", org_id=org_id, charge_point_id=charge_point_id)
        r = await self.httpx_client.post(
            f"https://client-api.evnex.io/v2/apps/organisations/{org_id}/charge-points/{charge_point_id}/commands/remote-stop-transaction",
            headers=self._common_headers,
            # 'Connection': 'Keep-Alive'
            json={"connectorId": connector_id},
            timeout=timeout,
        )
        json_data = await self._check_api_response(r)

        return EvnexCommandResponse.parse_obj(json_data["data"])
