from pydantic import BaseModel

from evnex.schema.org import EvnexOrgSummaryStatus


class EvnexOrgConnectorSummaryAttributes(BaseModel):
    # Same per-status connector counts as the flat EvnexOrgSummaryStatus, just
    # nested one level deeper in this endpoint's JSON:API-style response.
    connectors: EvnexOrgSummaryStatus


class EvnexOrgConnectorSummaryData(BaseModel):
    attributes: EvnexOrgConnectorSummaryAttributes


class EvnexGetOrgConnectorSummaryResponse(BaseModel):
    data: EvnexOrgConnectorSummaryData
