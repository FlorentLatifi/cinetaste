class AppError(Exception):
    """Base application error with HTTP-friendly metadata."""

    def __init__(self, message: str, *, status_code: int = 400, code: str = "app_error") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


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
