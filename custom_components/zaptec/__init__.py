"""Zaptec component."""

from __future__ import annotations

from collections.abc import Container
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_CHARGERS,
    CONF_MANUAL_SELECT,
    CONF_PREFIX,
    DOMAIN,
    REDACT_DUMP_ON_STARTUP,
    REDACT_LOGS,
    ZAPTEC_POLL_INTERVAL_BUILD,
    ZAPTEC_POLL_INTERVAL_CHARGING,
    ZAPTEC_POLL_INTERVAL_IDLE,
    ZAPTEC_POLL_INTERVAL_INFO,
)
from .coordinator import ZaptecUpdateCoordinator, ZaptecUpdateOptions
from .manager import ZaptecConfigEntry, ZaptecManager
from .services import async_setup_services, async_unload_services
from .zaptec import (
    RETRYABLE_HTTP_STATUSES,
    AuthenticationError,
    Installation,
    RequestConnectionError,
    RequestError,
    RequestTimeoutError,
    Zaptec,
    ZaptecApiError,
)

_LOGGER = logging.getLogger(__name__)


def _config_entry_error(
    err: ZaptecApiError,
) -> ConfigEntryAuthFailed | ConfigEntryNotReady | ConfigEntryError:
    """Map a Zaptec API error from setup login to a HA config-entry error.

    Authentication failures are non-recoverable (trigger re-auth). Connection
    and timeout errors, and transient server statuses (see
    RETRYABLE_HTTP_STATUSES), are recoverable, so we raise ConfigEntryNotReady
    to let Home Assistant retry setup automatically instead of failing
    permanently (issue #392). All other API errors remain permanent
    ConfigEntryError failures.
    """
    if isinstance(err, AuthenticationError):
        return ConfigEntryAuthFailed(str(err))
    if isinstance(err, (RequestTimeoutError, RequestConnectionError)):
        return ConfigEntryNotReady(str(err))
    if isinstance(err, RequestError) and err.error_code in RETRYABLE_HTTP_STATUSES:
        return ConfigEntryNotReady(str(err))
    return ConfigEntryError(str(err))


PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.UPDATE,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up zaptec as config entry."""

    redacted_data = {**entry.data}
    for key in ("password", "username"):
        if key in redacted_data:
            redacted_data[key] = "********"

    _LOGGER.debug("Setting up entry %s: %s", entry.entry_id, redacted_data)

    # Remove deprecated entities where we need to reuse the entity_ids
    remove_deprecated_entities(hass, entry)

    configured_chargers = None
    if entry.data.get(CONF_MANUAL_SELECT, False):
        configured_chargers = entry.data.get(CONF_CHARGERS)

    prefix = entry.data.get(CONF_PREFIX, "").rstrip()
    if prefix:
        prefix = prefix + " "

    # Create the Zaptec object
    zaptec = Zaptec(
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        client=async_get_clientsession(hass),
        max_time=ZAPTEC_POLL_INTERVAL_CHARGING,  # The shortest of the intervals
        show_all_updates=True,  # During setup we'd like to log all updates
        redact_logs=REDACT_LOGS,
    )

    # Login to the Zaptec account
    try:
        await zaptec.login()
    except ZaptecApiError as err:
        _LOGGER.error("Zaptec login failed: %s", err)
        raise _config_entry_error(err) from err

    # Get the structure of devices from Zaptec and determine the zaptec objects to track
    tracked_devices, all_selected_present = await ZaptecManager.first_time_setup(
        zaptec=zaptec,
        configured_chargers=configured_chargers,
    )

    # Setup the manager which will be where all the instance data is stored
    manager = ZaptecManager(
        hass,
        entry=entry,
        zaptec=zaptec,
        name_prefix=prefix,
        tracked_devices=tracked_devices,
    )

    # Setup the head update coordinator
    manager.head_coordinator = ZaptecUpdateCoordinator(
        hass,
        entry=entry,
        manager=manager,
        options=ZaptecUpdateOptions(
            name="zaptec",
            update_interval=ZAPTEC_POLL_INTERVAL_BUILD,
            charging_update_interval=None,
            tracked_devices=manager.tracked_devices,
            poll_args={"state": False, "info": False, "firmware": True},
            zaptec_object=None,
        ),
    )
    # Dummy listener to ensure the coordinator runs
    manager.head_coordinator.async_add_listener(lambda: None)

    # Setup the info update coordinator
    manager.info_coordinator = ZaptecUpdateCoordinator(
        hass,
        entry=entry,
        manager=manager,
        options=ZaptecUpdateOptions(
            name="info",
            update_interval=ZAPTEC_POLL_INTERVAL_INFO,
            charging_update_interval=None,
            tracked_devices=manager.tracked_devices,
            poll_args={"state": False, "info": True, "firmware": False},
            zaptec_object=None,
        ),
    )
    # Dummy listener to ensure the coordinator runs
    manager.info_coordinator.async_add_listener(lambda: None)

    # Setup the device coordinators for each tracked device
    for deviceid in tracked_devices:
        zaptec_obj = zaptec[deviceid]

        if isinstance(zaptec_obj, Installation):
            # Since installations do not have a state, we only poll the info endpoint.
            # The polling interval for installations does not change when charging.
            poll_args = {"state": False, "info": True, "firmware": False}
            charging_update_interval = None
        else:
            # Only chargers will have an alternate update interval when charging
            poll_args = {"state": True, "info": False, "firmware": False}
            charging_update_interval = ZAPTEC_POLL_INTERVAL_CHARGING

        manager.device_coordinators[deviceid] = ZaptecUpdateCoordinator(
            hass,
            entry=entry,
            manager=manager,
            options=ZaptecUpdateOptions(
                name=zaptec_obj.qual_id,
                update_interval=ZAPTEC_POLL_INTERVAL_IDLE,
                charging_update_interval=charging_update_interval,
                tracked_devices={deviceid},  # One device per coordinator
                poll_args=poll_args,
                zaptec_object=zaptec_obj,
            ),
        )

    # Initialize the coordinators
    for co in manager.all_coordinators:
        await co.async_config_entry_first_refresh()

    # Done setting up, change back to not log all updates. Having this enabled
    # will create a lot of debug log output.
    zaptec.show_all_updates = False
    _LOGGER.debug("Zaptec setup complete")

    # Dump the redaction database
    if REDACT_LOGS and REDACT_DUMP_ON_STARTUP:
        message = "Redaction database:\n------  DO NOT PUBLISH THE FOLLOWING DATA  ------\n"
        message += manager.zaptec.redact.dumps()
        message += "\n------  DO NOT PUBLISH THE ABOVE ^^^ DATA  ------"
        _LOGGER.debug(message)

    # Attach the local data to the HA config entry so it can be accessed later
    # in various HA functions.
    entry.runtime_data = manager

    # Setup services
    await async_setup_services(hass, manager)

    # Setup all platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    # Setup the streams
    manager.create_streams()

    # Make a set of the circuit ids from zaptec to check for deprecated Circuit-devices
    circuit_ids = {cid for c in manager.zaptec.chargers if (cid := c.get("CircuitId"))}

    # Removal needs both: track-all mode has no deselection to detect, and a partial
    # API response is indistinguishable from one.
    manual_select = configured_chargers is not None
    check_untracked = manual_select and all_selected_present

    if manual_select and not all_selected_present:
        _LOGGER.warning(
            "One or more selected chargers were not returned by the Zaptec API "
            "this session; skipping removal of untracked devices to avoid deleting "
            "a still-selected charger due to a transient/partial response"
        )

    _cleanup_devices(
        hass,
        entry,
        manager.tracked_devices,
        circuit_ids,
        check_untracked=check_untracked,
    )

    return True


def _cleanup_devices(
    hass: HomeAssistant,
    entry: ZaptecConfigEntry,
    tracked_devices: Container[str],
    circuit_ids: Container[str],
    check_untracked: bool,
) -> None:
    """Remove device entries that no longer belong to this config entry.

    Handles devices with no entities, deprecated Circuit-devices, and - only when
    `check_untracked` - devices whose zaptec id is no longer tracked. Passing
    `check_untracked` while `tracked_devices` may be incomplete would permanently
    delete a still-selected charger's device.
    """
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    device_entries = dr.async_entries_for_config_entry(
        device_registry, config_entry_id=entry.entry_id
    )
    for dev in device_entries:
        dev_entities = er.async_entries_for_device(
            entity_registry, dev.id, include_disabled_entities=True
        )
        if not dev_entities:
            device_registry.async_remove_device(dev.id)
            continue
        for domain, zap_dev_id in dev.identifiers:
            # A foreign id is never in tracked_devices, so without this the
            # untracked check would delete the device.
            if domain != DOMAIN:
                continue
            if zap_dev_id in circuit_ids:
                _LOGGER.warning(
                    "Detected deprecated Circuit device %s, "
                    "removing device and associated entities",
                    zap_dev_id,
                )
            elif check_untracked and zap_dev_id not in tracked_devices:
                _LOGGER.warning(
                    "Detected stale device %s no longer selected, "
                    "removing device and associated entities",
                    zap_dev_id,
                )
            else:
                continue

            for ent in dev_entities:
                _LOGGER.debug("Deleting entity %s", ent.entity_id)
                entity_registry.async_remove(ent.entity_id)
            device_registry.async_remove_device(dev.id)
            # Removing twice would raise KeyError, so stop at the first matching id.
            break


def remove_deprecated_entities(hass: HomeAssistant, entry: ZaptecConfigEntry) -> None:
    """Remove deprecated entites from the entity_registry."""

    entity_registry = er.async_get(hass)
    zaptec_entity_list = [
        (entity_id, entity)
        for entity_id, entity in list(entity_registry.entities.items())
        if entity.config_entry_id == entry.entry_id
    ]
    for entity_id, entity in zaptec_entity_list:
        if entity.translation_key == "operating_mode":
            # Needed for v0.7 -> v0.8 upgrade
            # The two entities this applies to were changed to the key
            # charger_operation_mode (from state) instead of operating_mode (from info).
            # In order to keep the same entity_id, we need to remove the old entries
            # before the new ones are added.
            _LOGGER.warning("Removing deprecated entity: %s", entity_id)
            entity_registry.async_remove(entity_id)
        elif entity.unique_id.endswith("_is_authorization_required"):
            # Needed for v0.7 -> v0.8 upgrade
            # There is an entity using authorization_required as a translation key in
            # both the installation and the charger device. We only want to replace the
            # entity associated with the charger, so we use the end of the unique_id
            # to find the correct entity that will be readded later (the installation
            # entity ends with '_is_required_authentication').
            _LOGGER.warning("Removing deprecated entity: %s", entity_id)
            entity_registry.async_remove(entity_id)


async def async_unload_entry(hass: HomeAssistant, entry: ZaptecConfigEntry) -> bool:
    """Unload a config entry."""

    manager = entry.runtime_data
    await manager.cancel_streams()
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    await async_unload_services(hass)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)
