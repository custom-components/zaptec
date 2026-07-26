"""Tests for custom_components.zaptec.__init__."""

from http import HTTPStatus
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError, ConfigEntryNotReady
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zaptec import _config_entry_error
from custom_components.zaptec.manager import ZaptecManager
from custom_components.zaptec.zaptec.exceptions import (
    AuthenticationError,
    RequestConnectionError,
    RequestError,
    RequestTimeoutError,
)
from tests.conftest import setup_integration


@pytest.mark.parametrize(
    ("err", "expected"),
    [
        # Bad credentials are non-recoverable -> re-auth flow.
        (AuthenticationError("bad"), ConfigEntryAuthFailed),
        # Connection/timeout are recoverable -> HA retries setup.
        (RequestTimeoutError("slow"), ConfigEntryNotReady),
        (RequestConnectionError("down"), ConfigEntryNotReady),
        # Transient server statuses are recoverable -> HA retries setup.
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


async def test_setup_entry_creates_manager_and_entities(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """A full setup wires up the manager and registers at least one entity."""
    manager = await setup_integration(hass, mock_config_entry, mock_zaptec)

    assert isinstance(manager, ZaptecManager)
    assert mock_config_entry.runtime_data is manager
    # HA slugifies each entity's name into its entity_id's object_id half; this
    # only matches because conftest.py's mock_zaptec seeds "Mock Charger"/"Mock
    # Home" (make_charger/make_installation), so every zaptec-created entity_id
    # starts with "mock". If that seed naming ever changes, update this filter.
    states = [s for s in hass.states.async_all() if s.entity_id.split(".")[1].startswith("mock")]
    assert states, "expected at least one zaptec entity to be created"
