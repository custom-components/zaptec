"""Tests for zaptec/api.py."""

import logging

import pytest

from custom_components.zaptec.zaptec.api import Zaptec

_LOGGER = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_api(zaptec_username: str, zaptec_password: str) -> None:
    """
    Test the Zaptec API.

    Does not run when testing in Github actions, since it requires login credentials.
    """

    logging.basicConfig(level=logging.DEBUG)
    logging.getLogger("azure").setLevel(logging.WARNING)

    async with Zaptec(zaptec_username, zaptec_password) as zaptec:
        # Builds the interface.
        await zaptec.login()
        await zaptec.build()
        await zaptec.poll(info=True, state=True, firmware=True)

        # Dump redaction database
        _LOGGER.info("Redaction database:")
        _LOGGER.info(zaptec.redact.dumps())

        # Print all the attributes.
        for obj in zaptec.objects():
            _LOGGER.info(obj.asdict())
