"""Zaptec testing configuration file."""

import asyncio
from collections.abc import Callable, Iterable
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zaptec.const import DOMAIN
from custom_components.zaptec.manager import ZaptecManager
from custom_components.zaptec.zaptec import MISSING, Charger, Installation
from custom_components.zaptec.zaptec.api import Zaptec
from custom_components.zaptec.zaptec.utils import to_under


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
    """Get latest constants from Zaptec API.

    Uses a self-contained event loop instead of `asyncio.run()`. Under
    pytest-homeassistant-custom-component's `HassEventLoopPolicy`,
    `asyncio.run()` unconditionally resets the thread's registered event
    loop to `None` on exit (success or failure) via `asyncio.set_event_loop`.
    That policy raises `RuntimeError` from `get_event_loop()` instead of
    lazily creating one, so the next bare `asyncio_mode=auto` test in the
    session would fail in its autouse loop-setup fixture. Saving/restoring
    the previous loop here keeps this fixture from clobbering global
    event-loop state for tests that run after it.
    """

    async def get_zaptec_constants() -> dict:
        async with Zaptec("N/A", "N/A") as zaptec:
            # the constants API endpoint does not require login
            const: dict = await zaptec.request("constants")
            return const

    try:
        previous_loop = asyncio.get_event_loop()
    except RuntimeError:
        previous_loop = None

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(get_zaptec_constants())
    finally:
        loop.close()
        asyncio.set_event_loop(previous_loop)


def _backed_get(data: dict[str, Any]) -> Callable[..., Any]:
    """Return a `.get(key, default=MISSING)` implementation backed by `data`.

    Mirrors `ZaptecBase.__getitem__`'s key normalization (`to_under`) so lookups
    behave the same whether `data` is hand-authored snake_case or seeded from a
    raw API payload. Still diverges from `ZaptecBase.get` (inherited from
    `Mapping.get`) in its own default: `MISSING` instead of `None`. Harmless in
    practice, since every real call site (`entity.py`'s `_get_zaptec_value`)
    always passes `default=MISSING` explicitly.
    """

    def _get(key: str, default: Any = MISSING) -> Any:
        return data.get(to_under(key), default)

    return _get


def make_charger(
    data: dict[str, Any], *, installation: MagicMock | None = None, charging: bool = False
) -> MagicMock:
    """Build a spec'd Charger double backed by `data`.

    `model` is hardcoded to the base `ZaptecBase.model`'s default format
    (`f"Zaptec {qualname}"`, api.py) — it does NOT model `Charger.model`'s real
    override, which looks up a device-ID-prefix in `ZCONST.serial_to_model`. A
    known, deliberate simplification, same category as `_backed_get`'s divergences.
    """
    charger = MagicMock(spec=Charger)
    charger.id = data["id"]
    charger.name = data.get("name", "Mock Charger")
    charger.model = "Zaptec Charger"
    charger.qual_id = f"Charger[{data['id'][-6:]}]"
    charger.get.side_effect = _backed_get(data)
    charger.is_charging.return_value = charging
    charger.installation = installation
    return charger


def make_installation(data: dict[str, Any], *, chargers: Iterable[MagicMock] = ()) -> MagicMock:
    """Build a spec'd Installation double backed by `data`."""
    install = MagicMock(spec=Installation)
    install.id = data["id"]
    install.name = data.get("name", "Mock Installation")
    install.model = "Zaptec Installation"
    install.qual_id = f"Installation[{data['id'][-6:]}]"
    install.get.side_effect = _backed_get(data)
    install.chargers = list(chargers)
    install.stream_main = AsyncMock(return_value=None)
    install.stream_close = AsyncMock(return_value=None)
    return install


@pytest.fixture
def mock_zaptec() -> MagicMock:
    """A spec'd Zaptec client seeded with one installation and one charger.

    The `__getitem__`/`__iter__`/`__contains__`/`__len__` wiring isn't arbitrary
    mock scaffolding: `Zaptec` is itself `Mapping[str, ZaptecBase]` in production
    (api.py), and real code indexes into it directly (e.g. `zaptec[deviceid]` in
    `__init__.py`/`coordinator.py`).
    """
    installation = make_installation({"id": "inst-mock-1", "name": "Mock Home"})
    charger = make_charger(
        {
            "id": "chg-mock-1",
            "name": "Mock Charger",
            # Keys read by entities under test; extend as needed for coverage.
            "operating_mode": "Connected",
            "charger_operation_mode": "Connected",
        },
        installation=installation,
        charging=False,
    )
    installation.chargers = [charger]

    objects = {installation.id: installation, charger.id: charger}

    zaptec = MagicMock(spec=Zaptec)
    zaptec.__getitem__.side_effect = objects.__getitem__
    zaptec.__iter__.side_effect = lambda: iter(objects)
    zaptec.__contains__.side_effect = objects.__contains__
    zaptec.__len__.side_effect = lambda: len(objects)
    zaptec.objects.return_value = list(objects.values())
    zaptec.installations = [installation]
    zaptec.chargers = [charger]
    zaptec.login = AsyncMock(return_value=None)
    zaptec.build = AsyncMock(return_value=None)
    zaptec.poll = AsyncMock(return_value=None)
    zaptec.show_all_updates = False
    zaptec.redact = MagicMock()
    # Load-bearing, not incidental: __init__.py's startup debug-dump path does
    # `message += manager.zaptec.redact.dumps()`, which setup_integration actually
    # exercises. An unconfigured MagicMock here would raise TypeError on the +=.
    zaptec.redact.dumps.return_value = ""
    return zaptec


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """A MockConfigEntry for the zaptec domain."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Mock Zaptec",
        data={CONF_USERNAME: "user", CONF_PASSWORD: "pass"},
        entry_id="mock_entry_1",
    )


async def setup_integration(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_zaptec: MagicMock
) -> ZaptecManager:
    """Set the integration up through the real async_setup, with a mocked client.

    Patches `custom_components.zaptec.Zaptec`, not `custom_components.zaptec.zaptec.
    api.Zaptec` where the class is defined — the standard unittest.mock rule is to
    patch where a name is *looked up*, not where it's *defined*. `__init__.py` does
    `from .zaptec import Zaptec`, so it holds its own local reference; patching the
    original definition would silently do nothing here.
    """
    mock_config_entry.add_to_hass(hass)
    with patch("custom_components.zaptec.Zaptec", return_value=mock_zaptec):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
    return mock_config_entry.runtime_data
