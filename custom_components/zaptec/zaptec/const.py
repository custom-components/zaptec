"""Zaptec API integration constants."""

from __future__ import annotations


class Missing:
    """Singleton class representing a missing value."""


MISSING = Missing()
"""Singleton instance representing a missing value."""


TOKEN_URL = "https://api.zaptec.com/oauth/token"  # noqa: S105
API_URL = "https://api.zaptec.com/api/"
CONST_URL = "https://api.zaptec.com/api/constants"

API_RETRIES = 8  # Corresponds to median ~100 seconds of retries before giving up
"""Number of retries for API requests."""

API_RETRY_INIT_DELAY = 0.3
"""Initial delay for the first API retry."""

API_RETRY_FACTOR = 2.1
"""Factor for exponential backoff in API retries."""

API_RETRY_JITTER = 0.1
"""Jitter to add to the API retry delay to avoid thundering herd problem."""

API_RETRY_MAXTIME = 60
"""Maximum time to wait for API retries."""

API_TIMEOUT = 10
"""The maximum time to wait for a response from the API."""

API_RATELIMIT_PERIOD = 1
"""Period in seconds for the bursting API rate limit."""

API_RATELIMIT_MAX_REQUEST_RATE = 10
"""Maximum number of requests allowed per API rate limit period."""

MAX_DEBUG_TEXT_LEN_ON_500 = 150
"""Maximum text length to add to debug log without truncating."""

TRUTHY = ["true", "1", "on", "yes", 1, True]
FALSY = ["false", "0", "off", "no", 0, False]

# Charger state attributes that should be excluded from being set as class
# attributes. Use strings.
CHARGER_EXCLUDES = {
    "854",  # PilotTestResults
    "900",  # ProductionTestResults
    "980",  # MIDCalibration
}
