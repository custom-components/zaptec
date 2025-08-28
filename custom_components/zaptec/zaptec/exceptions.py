"""Zaptec exceptions."""


class ZaptecApiError(Exception):
    """Base exception for all Zaptec API errors."""


class AuthenticationError(ZaptecApiError):
    """Authenatication failed."""


class RequestError(ZaptecApiError):
    """Failed to get the results from the API."""

    def __init__(self, message: str, error_code: int) -> None:
        """Initialize the RequestError."""
        super().__init__(message)
        self.error_code = error_code


class RequestConnectionError(ZaptecApiError):
    """Failed to make the request to the API."""


class RequestTimeoutError(ZaptecApiError):
    """Failed to get the results from the API."""


class RequestRetryError(ZaptecApiError):
    """Retries too many times."""


class RequestDataError(ZaptecApiError):
    """Data is not valid."""
