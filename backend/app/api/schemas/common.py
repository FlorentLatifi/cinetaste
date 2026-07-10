from pydantic import BaseModel


class ErrorResponse(BaseModel):
    code: str
    message: str


class HealthResponse(BaseModel):
    status: str
    app: str
    env: str


class ReadyResponse(BaseModel):
    status: str
    database: str
    redis: str
