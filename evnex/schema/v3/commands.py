from pydantic import BaseModel


class EvnexCommandResponse(BaseModel):
    message: str | None = None
    status: str  # Accepted
