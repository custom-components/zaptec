"""Tests for custom_components.zaptec.__init__."""

from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError, ConfigEntryNotReady
import pytest

from custom_components.zaptec import _config_entry_error, async_setup
from custom_components.zaptec.zaptec.exceptions import (
    AuthenticationError,
    RequestConnectionError,
    RequestError,
    RequestTimeoutError,
)


@pytest.mark.parametrize(
    ("err", "expected"),
    [
        # Bad credentials are non-recoverable -> re-auth flow.
        (AuthenticationError("bad"), ConfigEntryAuthFailed),
        # Connection/timeout are recoverable -> HA retries setup.
        (RequestTimeoutError("slow"), ConfigEntryNotReady),
        (RequestConnectionError("down"), ConfigEntryNotReady),
        # Transient server statuses are recoverable -> HA retries setup (issue #392).
        (RequestError("unavailable", HTTPStatus.SERVICE_UNAVAILABLE), ConfigEntryNotReady),
        (RequestError("too many", HTTPStatus.TOO_MANY_REQUESTS), ConfigEntryNotReady),
        # Other HTTP errors stay permanent.
        (RequestError("forbidden", HTTPStatus.FORBIDDEN), ConfigEntryError),
        (RequestError("not found", HTTPStatus.NOT_FOUND), ConfigEntryError),
    ],
)
def test_config_entry_error_mapping(err: Exception, expected: type[Exception]) -> None:
    """Setup login errors map to the right Home Assistant config-entry error."""
    assert isinstance(_config_entry_error(err), expected)


async def test_async_setup_registers_services() -> None:
    """async_setup wires up zaptec's services once, independent of any config entry."""
    hass = MagicMock()

    with patch(
        "custom_components.zaptec.async_setup_services", new=AsyncMock()
    ) as mock_setup_services:
        result = await async_setup(hass, {})

    assert result is True
    mock_setup_services.assert_awaited_once_with(hass)
