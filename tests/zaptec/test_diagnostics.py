"""Tests for diagnostics.py."""

import asyncio
from dataclasses import dataclass
import logging
import os

import aiohttp
import pytest

from custom_components.zaptec.diagnostics import _get_diagnostics
from custom_components.zaptec.zaptec.api import Zaptec

# Always set to "true" when GitHub Actions is running a workflow
IN_GITHUB_ACTIONS = os.getenv("GITHUB_ACTIONS") == "true"

# Set manually, or when running 'scripts/test --skip-api'
SKIP_API_TEST = os.getenv("SKIP_ZAPTEC_API_TEST") == "true"

_LOGGER = logging.getLogger(__name__)


@pytest.mark.skipif(IN_GITHUB_ACTIONS, reason="Test doesn't work in Github Actions.")
@pytest.mark.skipif(SKIP_API_TEST, reason="User disabled the tests requiring API login.")
def test_diagnostics() -> None:
    """
    Test the Zaptec Integration diagnostics.

    Does not run when testing in Github actions, since it requires login credentials.
    """

    username = os.environ.get("ZAPTEC_USERNAME")
    password = os.environ.get("ZAPTEC_PASSWORD")

    assert username, (
        "Missing username, either set it with \"export ZAPTEC_USERNAME='username'\" "
        "or run test script with the --skip-api flag"
    )
    assert password, (
        "Missing password, either set it with \"export ZAPTEC_PASSWORD='password'\" "
        "or run test script with the --skip-api flag"
    )

    logging.basicConfig(level=logging.DEBUG)
    logging.getLogger("azure").setLevel(logging.WARNING)

    async def gogo() -> None:
        async with (
            aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session,
            Zaptec(username, password, client=session) as zaptec,
        ):
            await zaptec.build()

            #
            # Mocking to pretend to be a hass instance
            # NOTE: This fails on getting entity lists in the output, but that's
            # ok for this test.
            #
            @dataclass
            class FakeZaptecManager:
                zaptec: Zaptec

            @dataclass
            class FakeConfig:
                runtime_data: FakeZaptecManager

            manager = FakeZaptecManager(
                zaptec=zaptec,
            )
            config = FakeConfig(
                runtime_data=manager,
            )
            hass = None

            # Get the diagnostics info
            out = await _get_diagnostics(hass, config)
            _LOGGER.info(out)

    asyncio.run(gogo())
