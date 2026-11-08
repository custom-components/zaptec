"""Zaptec exceptions."""


class ZaptecApiError(Exception):
    """Base exception for all Zaptec API errors."""


class AuthenticationError(ZaptecApiError):
    """Authenatication failed."""


class RequestError(ZaptecApiError):
    """Failed to get the results from the API."""

    def __init__(
        self,
        message: str,
        error_code: int,
        zaptec_code: int | None = None,
        zaptec_details: str | None = None,
    ) -> None:
        """Initialize the RequestError.

        `error_code` is the HTTP status. `zaptec_code` is Zaptec's own error
        code from the response body, which is only sent on HTTP 500, and
        `zaptec_details` its optional human-readable explanation.
        """
        super().__init__(message)
        self.error_code = error_code
        self.zaptec_code = zaptec_code
        self.zaptec_details = zaptec_details


class RequestConnectionError(ZaptecApiError):
    """Failed to make the request to the API."""


class RequestTimeoutError(ZaptecApiError):
    """Failed to get the results from the API."""


class RequestRetryError(ZaptecApiError):
    """Retries too many times."""


class RequestDataError(ZaptecApiError):
    """Data is not valid."""
