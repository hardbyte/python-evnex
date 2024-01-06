from typing import Optional

try:
    from pydantic.v1 import BaseModel
except ImportError:
    from pydantic import BaseModel


class EvnexCommandResponse(BaseModel):
    message: Optional[str]
    status: str  # Accepted
