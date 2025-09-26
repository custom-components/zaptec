"""Tests for diagnostics.py."""

from dataclasses import dataclass
import logging

import aiohttp
import pytest

from custom_components.zaptec.diagnostics import _get_diagnostics
from custom_components.zaptec.zaptec.api import Zaptec

_LOGGER = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_diagnostics(zaptec_username: str, zaptec_password: str) -> None:
    """
    Test the Zaptec Integration diagnostics.

    Does not run when testing in Github actions, since it requires login credentials.
    """

    logging.basicConfig(level=logging.DEBUG)
    logging.getLogger("azure").setLevel(logging.WARNING)

    async with (
        aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session,
        Zaptec(zaptec_username, zaptec_password, client=session) as zaptec,
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
