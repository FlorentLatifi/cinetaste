class AppError(Exception):
    """Base application error with HTTP-friendly metadata."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 400,
        code: str = "app_error",
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        # Response headers the client needs to act on the error (e.g. Retry-After).
        self.headers = headers or {}


class NotFoundError(AppError):
    def __init__(self, message: str = "Resource not found", *, code: str = "not_found") -> None:
        super().__init__(message, status_code=404, code=code)


class ConflictError(AppError):
    def __init__(self, message: str, *, code: str = "conflict") -> None:
        super().__init__(message, status_code=409, code=code)


class UnauthorizedError(AppError):
    def __init__(self, message: str = "Unauthorized", *, code: str = "unauthorized") -> None:
        super().__init__(message, status_code=401, code=code)


class ForbiddenError(AppError):
    def __init__(self, message: str = "Forbidden", *, code: str = "forbidden") -> None:
        super().__init__(message, status_code=403, code=code)


class RateLimitedError(AppError):
    """Too many attempts for one identity (account), independent of source IP.

    The IP-keyed middleware limit cannot be trusted on its own: the API origin
    is reachable directly, so a client can choose its own X-Forwarded-For and
    therefore its own bucket. Throttling per account closes that hole and also
    covers brute force spread across many source addresses.
    """

    def __init__(
        self,
        message: str = "Too many attempts. Please try again later.",
        *,
        retry_after: int = 60,
        code: str = "rate_limited",
    ) -> None:
        super().__init__(
            message,
            status_code=429,
            code=code,
            headers={"Retry-After": str(max(int(retry_after), 1))},
        )
