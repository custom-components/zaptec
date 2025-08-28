"""HA Entity for Zaptec integration."""

from __future__ import annotations

from collections.abc import Iterable
import logging
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.entity import DeviceInfo, EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import KEYS_TO_SKIP_ENTITY_AVAILABILITY_CHECK
from .coordinator import ZaptecUpdateCoordinator
from .zaptec import MISSING, Missing, ZaptecBase

_LOGGER = logging.getLogger(__name__)


class KeyUnavailableError(Exception):
    """Exception raised when a key is not available in the Zaptec object."""

    def __init__(self, key: str, message: str) -> None:
        """Initialize the KeyUnavailableError."""
        super().__init__(message)
        self.key = key


class ZaptecBaseEntity(CoordinatorEntity[ZaptecUpdateCoordinator]):
    """Base class for Zaptec entities."""

    coordinator: ZaptecUpdateCoordinator
    zaptec_obj: ZaptecBase
    entity_description: EntityDescription
    _attr_has_entity_name = True
    _prev_value: Any = MISSING
    _prev_available: bool = True  # Assume the entity is available at start for logging
    _log_attribute: str | None = None
    """The attribute to log when the value changes."""

    def __init__(
        self,
        coordinator: ZaptecUpdateCoordinator,
        zaptec_object: ZaptecBase,
        description: EntityDescription,
        device_info: DeviceInfo,
    ) -> None:
        """Initialize the Zaptec entity."""
        super().__init__(coordinator)

        self.zaptec_obj = zaptec_object
        self.entity_description = description
        self._attr_unique_id = f"{zaptec_object.id}_{description.key}"
        self._attr_device_info = device_info

        # Set the zaptec attribute for logging. Inheriting classes can override
        # this to change the default behavior. None means that the entity
        # doesn't use any attributes from Zaptec.
        if not hasattr(self, "_log_zaptec_key"):
            self._log_zaptec_key = description.key

        # Call this last if the inheriting class needs to do some addition
        # initialization
        self._post_init()

    def _post_init(self) -> None:
        """Post-initialization method for the entity.

        Called after the entity has been initialized. Implement this for a
        custom light-weight init in the inheriting class.
        """

    @callback
    def _handle_coordinator_update(self) -> None:
        """Update the entity from Zaptec data.

        If the class have an attribute callback `_update_from_zaptec`, it will
        be called to update the entity data from the Zaptec data. The method is
        expected to call `_get_zaptec_value()` to retrieve the value for the
        entity, which may raise `KeyUnavailableError` if the key is not
        available. This function will log the value if it changes or becomes
        unavailable.
        """
        update_from_zaptec = getattr(self, "_update_from_zaptec", lambda: None)
        try:
            update_from_zaptec()
            self._log_value(self._log_attribute)
            self._log_unavailable()  # For logging when the entity becomes available again
        except KeyUnavailableError as exc:
            self._attr_available = False
            self._log_unavailable(exc)
        super()._handle_coordinator_update()

    @callback
    def _get_zaptec_value(
        self, *, default: Any | Missing = MISSING, key: str = "", lower_case_str: bool = False
    ) -> Any:
        """Retrieve a value from the Zaptec object.

        Helper to retrieve the value from the Zaptec object. This is to
        be called from _handle_coordinator_update() in the inheriting class.
        It will fetch the attr given by the entity description key.

        Raises:
            KeyUnavailableError: If key doesn't exist or obj doesn't have
            `.get()`, which indicates that obj isn't a Mapping-like object

        """
        obj = self.zaptec_obj
        key = key or self.key
        for k in key.split("."):
            try:
                obj = obj.get(k, default)
            except AttributeError:
                # This means that obj doesn't have `.get()`, which indicates that obj isn't a
                # a Mapping-like object.
                suffix = f". Failed getting {k!r}" if k != key else ""
                raise KeyUnavailableError(
                    key, f"Failed to retrieve {key!r} from {self.zaptec_obj.qual_id}{suffix}"
                ) from None
            if obj is MISSING:
                suffix = f". Key {k!r} doesn't exitst" if k != key else ""
                raise KeyUnavailableError(
                    key, f"Failed to retrieve {key!r} from {self.zaptec_obj.qual_id}{suffix}"
                )
            if obj is default:
                return obj
        if isinstance(obj, str) and lower_case_str:
            # If the value is a string, convert it to lower case if requested
            obj = obj.lower()
        return obj

    @property
    def _log_zaptec_attribute(self) -> str:
        """Get the zaptec attribute name for logging."""
        v = self._log_zaptec_key
        if v is None:
            return ""
        if isinstance(v, str):
            return f".{v}"
        if isinstance(v, Iterable):
            return "." + " and .".join(v)
        return f".{v}"

    @callback
    def _log_value(self, attribute: str | None, force: bool = False) -> None:
        """Log a new value."""
        if attribute is None:
            return
        value = getattr(self, attribute, MISSING)

        # Only logs when the value changes
        if force or value != self._prev_value:
            self._prev_value = value
            _LOGGER.debug(
                "    %s  =  %r <%s>   from %s%s",
                self.entity_id,
                value,
                type(value).__qualname__,
                self.zaptec_obj.qual_id,
                self._log_zaptec_attribute,
            )

    @callback
    def _log_unavailable(self, exception: Exception | None = None) -> None:
        """Log entity when unavailable."""
        available = self._attr_available
        prev_available = self._prev_available
        self._prev_available = available

        # Log when the entity becomes unavailable
        if prev_available and not available:
            _LOGGER.info("Entity %s is unavailable", self.entity_id)
            _LOGGER.debug(
                "    %s  =  UNAVAILABLE   from %s%s%s",
                self.entity_id,
                self.zaptec_obj.qual_id,
                self._log_zaptec_attribute,
                f"   (Error: {exception})" if exception else "",
            )

            # Dump the exception if present - not interested in KeyUnavailableError
            # since the TB is expected when the key is not available.
            if (
                exception is not None
                and not isinstance(exception, KeyUnavailableError)
                and self.key not in KEYS_TO_SKIP_ENTITY_AVAILABILITY_CHECK
            ):
                _LOGGER.error("Getting value failed", exc_info=exception)

        # Log when the entity becomes available again
        elif not prev_available and available:
            _LOGGER.info("Entity %s is available", self.entity_id)

    @property
    def key(self) -> str:
        """Helper to retrieve the key from the entity description."""
        return self.entity_description.key

    async def trigger_poll(self) -> None:
        """Trigger a poll for this entity."""
        await self.coordinator.trigger_poll()
