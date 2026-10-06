"""Tests for custom_components.zaptec.authorization."""

import pytest

from custom_components.zaptec.authorization import authorization_label, is_authorized

REMOTE_USER = "ble-4f2c1e08-9a5d-4b7e-8c31-0d6a5f9b2e77"
RFID_CARD = "nfc-04a1b2c3d4e5"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # An RFID card shows as-is: the token is all Zaptec gives us.
        (RFID_CARD, RFID_CARD),
        # A remote authorize is indistinguishable from the Zaptec app's.
        (REMOTE_USER, "HA/Zaptec App"),
        # Zaptec empties the observation at session end.
        ("", None),
        # Absent entirely on a charger with no session yet.
        (None, None),
        # Other prefixes pass through rather than being guessed at.
        ("app-1234", "app-1234"),
        # Observations are untyped, so a non-string can reach us.
        (1234, "1234"),
    ],
)
def test_authorization_label(raw: object, expected: str | None) -> None:
    """The raw token is mapped to what the user should see."""
    assert authorization_label(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (RFID_CARD, True),
        (REMOTE_USER, True),
        ("", False),
        (None, False),
        (1234, True),
    ],
)
def test_is_authorized(raw: object, expected: bool) -> None:
    """Any token means the session is authorized; no token means it is not."""
    assert is_authorized(raw) is expected
