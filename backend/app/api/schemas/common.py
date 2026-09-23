from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Request body that rejects fields it does not declare.

    Pydantic's default is to drop unknown keys silently. Nothing here is
    mass-assigned, so that was not exploitable — but a client sending
    ``{"new_pasword": "..."}`` got a 200 and no password change, and the
    only way to find out was to notice the effect was missing. Failing the
    request says so immediately.

    Only for request models. Responses stay permissive so adding a field
    cannot break a client that round-trips one back.
    """

    model_config = ConfigDict(extra="forbid")


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
    # redis | memory | memory (redis unavailable)
    cache: str
