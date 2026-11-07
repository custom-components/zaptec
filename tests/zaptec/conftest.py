"""Test configuration for the vendored Zaptec API client (tests/zaptec/*)."""

import asyncio

import pytest

from custom_components.zaptec.zaptec.api import Zaptec


@pytest.fixture(scope="session")
def zaptec_constants() -> dict:
    """Get latest constants from Zaptec API.

    Uses a self-contained event loop instead of `asyncio.run()`: under
    pytest-hacc's `HassEventLoopPolicy`, `asyncio.run()` resets the thread's
    event loop to `None` on exit, breaking later `asyncio_mode=auto` tests.
    Saving/restoring the previous loop avoids clobbering that global state.
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
