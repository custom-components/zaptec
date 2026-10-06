"""Interpretation of Zaptec's session authorization tokens."""

from __future__ import annotations

from typing import Any, Final

# Zaptec prefixes the token with the authorization method: "nfc-" for an RFID
# card, "ble-" followed by the authorizing user's uuid for a remote authorize.
REMOTE_AUTHORIZATION_PREFIX: Final = "ble-"

# A remote authorize made here is indistinguishable from one made in the app.
REMOTE_AUTHORIZATION_LABEL: Final = "HA/Zaptec App"


def is_authorized(raw: Any) -> bool:
    """Return whether a token authorizes the current session."""
    return bool(raw)


def authorization_label(raw: Any) -> str | None:
    """Return the label to display for a token, or None when there is none."""
    if not is_authorized(raw):
        return None
    token = str(raw)
    if token.startswith(REMOTE_AUTHORIZATION_PREFIX):
        return REMOTE_AUTHORIZATION_LABEL
    return token
