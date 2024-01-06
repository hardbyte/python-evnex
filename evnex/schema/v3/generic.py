from typing import Generic, Optional, TypeVar

try:
    from pydantic.v1 import BaseModel

    GenericModel = BaseModel
except ImportError:
    from pydantic import BaseModel
    from pydantic.generics import GenericModel

from evnex.schema.v3.relationships import EvnexRelationships

ResponseDataT = TypeVar("ResponseDataT")


class EvnexV3Include(BaseModel):
    id: str
    type: str
    attributes: dict


class EvnexV3Data(GenericModel, Generic[ResponseDataT]):
    id: str
    type: str
    attributes: ResponseDataT
    relationships: EvnexRelationships


class EvnexV3APIResponse(GenericModel, Generic[ResponseDataT]):
    data: EvnexV3Data[ResponseDataT]
    included: Optional[list[EvnexV3Include]]
