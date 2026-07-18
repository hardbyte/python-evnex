from pydantic import BaseModel


class EvnexConnectorSummary(BaseModel):
    available: int
    charging: int
    disabled: int
    faulted: int
    occupied: int
    offline: int
    reserved: int


class EvnexOrgConnectorSummaryAttributes(BaseModel):
    connectors: EvnexConnectorSummary


class EvnexOrgConnectorSummaryData(BaseModel):
    attributes: EvnexOrgConnectorSummaryAttributes


class EvnexGetOrgConnectorSummaryResponse(BaseModel):
    data: EvnexOrgConnectorSummaryData
