from pydantic import BaseModel


class EvnexCommandResponse(BaseModel):
    message: str
    status: str # Accepted
