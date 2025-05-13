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

API_RETRIES = 5
API_RETRY_FACTOR = 2.3
API_RETRY_JITTER = 0.1
API_RETRY_MAXTIME = 600
API_TIMEOUT = 10

DEFAULT_SCAN_INTERVAL = 60

# This sets the delay after doing actions and the poll of updated values.
# It was 0.3 and evidently that is a bit too fast for Zaptec cloud to handle.
REQUEST_REFRESH_DELAY = 1

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
