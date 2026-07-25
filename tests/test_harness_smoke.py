"""Smoke test: the real HA `hass` fixture spins up under the shim. Removed in Task 5."""

from homeassistant.core import HomeAssistant


async def test_hass_fixture_starts(hass: HomeAssistant) -> None:
    """The harness's real hass fixture is a live HomeAssistant with a working state machine."""
    assert isinstance(hass, HomeAssistant)
    hass.states.async_set("probe.entity", "on")
    await hass.async_block_till_done()
    assert hass.states.get("probe.entity").state == "on"
