"""Zaptec integration constants."""

from __future__ import annotations

NAME = "zaptec"
VERSION = "0.8.3"
ISSUEURL = "https://github.com/custom-components/zaptec/issues"

DOMAIN = "zaptec"
MANUFACTURER = "Zaptec"

REDACT_LOGS = True
"""Whether to redact sensitive data in logs."""

REDACT_DUMP_ON_STARTUP = True
"""Whether to dump the redaction database on startup."""

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

# These keys will not be checked at startup for entity availability. This is
# useful for keys that are not always present in the API response, such as
KEYS_TO_SKIP_ENTITY_AVAILABILITY_CHECK = {
    "three_to_one_phase_switch_current",
    "total_charge_power_session",
}
