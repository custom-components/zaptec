"""Tests for zaptec/api.py."""

import asyncio
import logging
import os

import pytest

from custom_components.zaptec.zaptec.api import Zaptec

# Always set to "true" when GitHub Actions is running a workflow
IN_GITHUB_ACTIONS = os.getenv("GITHUB_ACTIONS") == "true"

# Set manually, or when running 'scripts/test --skip-api'
SKIP_API_TEST = os.getenv("SKIP_ZAPTEC_API_TEST") == "true"

_LOGGER = logging.getLogger(__name__)


@pytest.mark.skipif(IN_GITHUB_ACTIONS, reason="Test doesn't work in Github Actions.")
@pytest.mark.skipif(SKIP_API_TEST, reason="User disabled the API test.")
def test_api() -> None:
    """
    Test the Zaptec API.

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
        async with Zaptec(username, password) as zaptec:
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

    asyncio.run(gogo())
