"""Platform for switch integration."""

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity

from . import controller
from .const import DOMAIN, MANUFACTURER, MODE_MRAD

LOGGER = logging.getLogger(__package__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Add switches for passed config_entry in HA."""
    LOGGER.debug("Adding MMS switch entities.")

    client = hass.data[DOMAIN][entry.entry_id]

    new_devices = []

    if client._mode == MODE_MRAD:
        LOGGER.debug("Adding Alloff button")
        new_devices.append( MmsAllOffButton(entry, hass, client) )

    if new_devices:
        async_add_entities(new_devices)


class MmsAllOffButton(ButtonEntity):

    def __init__(self, entry: ConfigEntry, hass: HomeAssistant, controller: controller.Controller):
        # Member variables that will never need to change
        self._hass = hass
        self._controller = controller

        self._attr_name = "All off"
        self.entity_id = f"button.{controller._name.lower().replace('-', '_')}_all_off"
        self._attr_unique_id = f"{entry.unique_id}_all_off"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id)},
            manufacturer=MANUFACTURER,
            model=self._controller._name,
            name=self._attr_name
        )

    @property
    def should_poll(self) -> bool:
        """Return True if entity has to be polled for state.
        False if entity pushes its state to HA.
        """
        return False

    def press(self) -> None:
        """Send a button press event."""
        self._controller.send("mrad.AllOff")
