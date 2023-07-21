NAME = "zaptec"
VERSION = "0.0.6b2"
ISSUEURL = "https://github.com/custom-components/zaptec/issues"

STARTUP = """
-------------------------------------------------------------------
{name}
Version: {version}
This is a custom component
If you have any issues with this you need to open an issue here:
{issueurl}
-------------------------------------------------------------------
""".format(
    name=NAME, version=VERSION, issueurl=ISSUEURL
)

DOMAIN = "zaptec"
OBSERVATIONS_REMAPS = {}
WANTED_ATTRIBUTES = []
CHARGE_MODE_MAP = {
    "0": ["unknown", "mdi:help-rhombus-outline"],
    "1": ["disconnected", "mdi:power-plug-off"],
    "2": ["waiting", "mdi:power-sleep"],
    "3": ["charging", "mdi:power-plug"],
    "5": ["charge_done", "mdi:battery-charging-100"],
}
CHARGE_MODE_MAP.update({int(k): v for k, v in CHARGE_MODE_MAP.items()})

TOKEN_URL = "https://api.zaptec.com/oauth/token"
API_URL = "https://api.zaptec.com/api/"
CONST_URL = "https://api.zaptec.com/api/constants"


CONF_SENSOR = "sensor"
CONF_SWITCH = "switch"
CONF_ENABLED = "enabled"
CONF_NAME = "name"

EVENT_NEW_DATA = "event_new_data_zaptec"
EVENT_NEW_DATA_HOURLY = "event_new_data_hourly_zaptec"
# PLATFORMS = ["sensor"]
PLATFORMS = ["sensor", "switch"]
