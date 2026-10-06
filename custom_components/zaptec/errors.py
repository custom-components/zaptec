"""Translated Home Assistant errors for failed Zaptec API calls."""

from __future__ import annotations

from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .zaptec.exceptions import RequestError
from .zaptec.zconst import ZCONST

# Zaptec error codes (see `ErrorCodes` in the API constants) that need wording
# of their own because the API sends no Details along with them.
CODE_DEVICE_COMMAND_REJECTED = 528
CODE_OPERATION_FAILED_DUE_TO_CHARGER_STATE = 538

# Details phrases Zaptec sends when the installation's charging mode blocks an
# update: "Automatic" (managed by Zaptec Sense) and "Scheduled" in the Zaptec
# app. Matched on the phrase so that any other reason falls through and is
# shown verbatim rather than being attributed to the wrong mode.
APM_IN_USE = "when using APM"
SCHEDULED_IN_USE = "scheduled power management"


def api_call_error(exc: Exception, action: str) -> HomeAssistantError:
    """Return a translated error explaining why a Zaptec API call failed.

    Zaptec reports the reason for a rejected call as an error code in the body
    of an HTTP 500, sometimes with human-readable Details. `action` describes
    what was attempted, e.g. "Set current limit to 6.0".
    """
    code = exc.zaptec_code if isinstance(exc, RequestError) else None
    details = exc.zaptec_details if isinstance(exc, RequestError) else None
    if not isinstance(details, str):
        details = None  # Details comes straight from the API, so don't trust its type

    placeholders = {"action": action}
    if code == CODE_DEVICE_COMMAND_REJECTED:
        key = "api_error_command_rejected"
    elif code == CODE_OPERATION_FAILED_DUE_TO_CHARGER_STATE:
        key = "api_error_charger_state"
    elif details and APM_IN_USE in details:
        # Zaptec calls it APM in the API, but users know it as Zaptec Sense.
        key = "api_error_apm"
    elif details and SCHEDULED_IN_USE in details:
        key = "api_error_scheduled"
    elif details:
        key = "api_error_details"
        placeholders["details"] = details
    elif code is not None:
        key = "api_error_code"
        placeholders["reason"] = ZCONST.type_error_code(code)
    else:
        key = "api_error"

    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders=placeholders,
    )
