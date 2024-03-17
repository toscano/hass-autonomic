"""Platform for switch integration."""

import logging
from typing import Any

from homeassistant.components.switch import (
    SwitchEntity,
    SwitchDeviceClass
)

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
        LOGGER.debug("Adding GroupVolume switch")
        new_devices.append( MmsGroupVolumeSwitch(entry, hass, client) )

    if new_devices:
        async_add_entities(new_devices)


class MmsGroupVolumeSwitch(SwitchEntity, RestoreEntity):

    def __init__(self, entry: ConfigEntry, hass: HomeAssistant, controller: controller.Controller):
        # Member variables that will never need to change
        self._hass = hass
        self._controller = controller

        self._attr_name = "Group volume"
        self.entity_id = f"switch.{controller._name.lower().replace('-', '_')}_group_volume"
        self._attr_unique_id = f"{entry.unique_id}_group_volume"

        self._attr_device_class = SwitchDeviceClass.SWITCH

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id)},
            manufacturer=MANUFACTURER,
            model=self._controller._name,
            name=self._attr_name
        )

        self._controller.add_switch_entity(self)

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added."""
        await super().async_added_to_hass()
        if not (last_state := await self.async_get_last_state()):
            return
        self._attr_is_on = last_state.state == STATE_ON
        self._controller.perform_group_volumes = self._attr_is_on
        self.update_ha()

    def turn_on(self, **kwargs: Any) -> None:
        """Turn on the switch."""
        self._attr_is_on = True
        self._controller.perform_group_volumes = self._attr_is_on
        self.update_ha()

    def turn_off(self, **kwargs: Any) -> None:
        self._attr_is_on = False
        self._controller.perform_group_volumes = self._attr_is_on
        self.update_ha()

    def update_ha(self):
        try:
            self.schedule_update_ha_state()
        except Exception as error:  # pylint: disable=broad-except
            LOGGER.debug("State update failed.")

    @property
    def icon(self):
        # Our ICON
        return "mdi:volume-equal"

    @property
    def should_poll(self) -> bool:
        """Return True if entity has to be polled for state.
        False if entity pushes its state to HA.
        """
        return False

    @property
    def available(self) -> bool:
        self._attr_available = self._controller.is_connected
        return self._attr_available
