from typing import Optional

from pydantic import BaseModel


class EvnexCommandResponse(BaseModel):
    message: Optional[str]
    status: str  # Accepted

