"""Zaptec testing configuration file."""

import asyncio
import os
from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.zaptec.zaptec.api import Zaptec


class FakeConfigEntry:
    """Minimal stand-in for HA's ConfigEntry.

    Exposes only what coordinator.py and entity.py actually touch
    (`pref_disable_polling`, `async_on_unload`,
    `async_create_background_task`). A real ConfigEntry pulls in HA's full
    test-harness machinery, which cannot run on native Windows in this dev
    environment - see CLAUDE.md's environment notes.
    """

    pref_disable_polling = False
    title = "Mock Title"

    def async_on_unload(self, func: Any) -> None:
        """No-op stand-in for HA's unload-callback registration. Never invoked by these tests."""

    def async_create_background_task(
        self, hass: Any, target: Any, name: str, eager_start: bool = True
    ) -> asyncio.Task:
        """Schedule target as a real asyncio Task.

        This ensures trigger_poll()'s cancel-and-replace logic is genuinely
        exercised by tests.
        """
        return asyncio.ensure_future(target)


@pytest.fixture
def config_entry() -> FakeConfigEntry:
    """A fake config entry for coordinator/entity tests."""
    return FakeConfigEntry()


@pytest.fixture
async def hass() -> MagicMock:
    """A minimal fake HomeAssistant object exposing a real running event loop.

    DataUpdateCoordinator only reads `hass.loop` (to schedule refreshes via
    `loop.call_at()`/`loop.time()`); coordinator.py and entity.py never touch
    any other HomeAssistant functionality (config, states, services, etc.).
    `is_stopping` is pinned False to match a real (non-shutting-down)
    HomeAssistant instance, since a bare MagicMock would otherwise be truthy.
    """
    fake_hass = MagicMock()
    fake_hass.loop = asyncio.get_running_loop()
    fake_hass.is_stopping = False
    return fake_hass


@pytest.fixture(scope="session")
def skip_if_in_github_actions() -> None:
    """Check if we are running in Github actions and skip any dependant tests if true."""
    if os.getenv("GITHUB_ACTIONS") == "true":
        pytest.skip("This test doesn't work in Github Actions.")


@pytest.fixture(scope="session")
def skip_if_user_disabled_api_tests() -> None:
    """Check if user has disabled API tests and skip any dependant tests if true."""
    if os.getenv("SKIP_ZAPTEC_API_TEST") == "true":
        pytest.skip("User disabled the tests requiring API login.")


@pytest.fixture(scope="session")
def zaptec_username(skip_if_user_disabled_api_tests, skip_if_in_github_actions) -> str:  # noqa: ANN001 (the inputs are purely to create dependencies to the env-flags above)
    """
    Get the zaptec username stored in env.

    Any test relying on this fixture will be skipped if the test is running
    in Gihub Actions, or the user has disabled tests requiring API login.
    """
    username = os.environ.get("ZAPTEC_USERNAME")
    assert username, (
        "Missing username, either set it with \"export ZAPTEC_USERNAME='username'\" "
        "or run test script with the --skip-api flag"
    )
    return username


@pytest.fixture(scope="session")
def zaptec_password(skip_if_user_disabled_api_tests, skip_if_in_github_actions) -> str:  # noqa: ANN001
    """
    Get the zaptec password stored in env.

    Any test relying on this fixture will be skipped if the test is running
    in Gihub Actions, or the user has disabled tests requiring API login.
    """
    password = os.environ.get("ZAPTEC_PASSWORD")
    assert password, (
        "Missing password, either set it with \"export ZAPTEC_PASSWORD='password'\" "
        "or run test script with the --skip-api flag"
    )
    return password


@pytest.fixture(scope="session")
def zaptec_constants() -> dict:
    """Get latest constants from Zaptec API."""

    async def get_zaptec_constants() -> dict:
        async with Zaptec("N/A", "N/A") as zaptec:
            # the constants API endpoint does not require login
            const: dict = await zaptec.request("constants")
            return const

    return asyncio.run(get_zaptec_constants())
