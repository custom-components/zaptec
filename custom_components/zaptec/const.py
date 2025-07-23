"""Zaptec integration constants."""

from __future__ import annotations

NAME = "zaptec"
VERSION = "0.7.4"
ISSUEURL = "https://github.com/custom-components/zaptec/issues"

DOMAIN = "zaptec"
MANUFACTURER = "Zaptec"

TOKEN_URL = "https://api.zaptec.com/oauth/token"
API_URL = "https://api.zaptec.com/api/"
CONST_URL = "https://api.zaptec.com/api/constants"

API_RETRIES = 9  # Corresponds to median ~31 seconds of retries before giving up
"""Number of retries for API requests."""

API_RETRY_INIT_DELAY = 0.01
"""Initial delay for the first API retry."""

API_RETRY_FACTOR = 2.3
"""Factor for exponential backoff in API retries."""

API_RETRY_JITTER = 0.1
"""Jitter to add to the API retry delay to avoid thundering herd problem."""

API_RETRY_MAXTIME = 600
"""Maximum time to wait for API retries."""

API_TIMEOUT = 10
"""The maximum time to wait for a response from the API."""

API_RATELIMIT_PERIOD = 1
"""Period in seconds for the bursting API rate limit."""

API_RATELIMIT_MAX_REQUEST_RATE = 10
"""Maximum number of requests allowed per API rate limit period."""

ZAPTEC_POLL_INTERVAL_IDLE = 10 * 60
""" Interval in seconds for polling the state from the API."""

ZAPTEC_POLL_INTERVAL_CHARGING = 60
""" Interval in seconds for polling the state from the API."""

ZAPTEC_POLL_INTERVAL_INFO = 60 * 60
"""Interval in seconds for polling the device info from the API."""

ZAPTEC_POLL_INTERVAL_BUILD = 24 * 60 * 60
"""Interval in seconds for polling the account-wide info from the API."""

ZAPTEC_POLL_CHARGER_TRIGGER_DELAYS = [2, 7, 15]
"""Delays in seconds for charger state updates after a change."""

ZAPTEC_POLL_INSTALLATION_TRIGGER_DELAYS = [2, 7]
"""Delays in seconds for installation state updates after a change."""

# This sets the delay after doing actions and the poll of updated values.
# It was 0.3 and evidently that is a bit too fast for Zaptec cloud to handle.
REQUEST_REFRESH_DELAY = 1
"""Delay after doing actions and the poll of updated values."""

CONF_MANUAL_SELECT = "manual_select"
CONF_CHARGERS = "chargers"
CONF_PREFIX = "prefix"


class Missing:
    """Singleton class representing a missing value."""


MISSING = Missing()

TRUTHY = ["true", "1", "on", "yes", 1, True]
FALSY = ["false", "0", "off", "no", 0, False]

# Charger state attributes that should be excluded from being set as sensor
# attributes. Use strings.
CHARGER_EXCLUDES = {
    "854",  # PilotTestResults
    "900",  # ProductionTestResults
    "980",  # MIDCalibration
}

# These keys will not be checked at startup for entity availability. This is
# useful for keys that are not always present in the API response, such as
KEYS_TO_SKIP_ENTITY_AVAILABILITY_CHECK = {
    "total_charge_power_session",
}
