"""Platform for media_player integration."""

import logging

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    ATTR_TO_PROPERTY
)

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import Entity

from . import controller
from .const import DOMAIN, MANUFACTURER, MODE_MRAD, MODE_STANDALONE

LOGGER = logging.getLogger(__package__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Add media_players for passed config_entry in HA."""
    LOGGER.debug("Adding MMS media_player entities.")

    client = hass.data[DOMAIN][entry.entry_id]

    new_devices = []

    for index in client._zones:
        # we skip video outputs without a name or those whose name starts with a dot (.)
        LOGGER.debug(f"Adding Zone {index}")
        new_devices.append( MmsZone(entry, hass, client, f"{index}") )

    if new_devices:
        async_add_entities(new_devices)



class MmsZone(MediaPlayerEntity):
    """Our Media Player"""

    def __init__(self, entry: ConfigEntry, hass: HomeAssistant, controller: controller.Controller, indexOrName: str):
        """Initialize our Media Player"""
        self._hass = hass
        self._controller = controller
        self._extra_attributes = {}
        self._isOn = False

        if controller._mode == MODE_MRAD:
            self._name = f"Zone {int(indexOrName):02d}"
            self._attr_unique_id = f"{entry.unique_id}_zone_{int(indexOrName):02d}"
        elif controller._mode == MODE_STANDALONE:
            self._name = indexOrName
            self._attr_unique_id = f"{entry.unique_id}_{indexOrName}"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id)},
            manufacturer=MANUFACTURER,
            model=self._controller._name,
            name=self._name
        )


    def update_ha(self):
        try:
            self.schedule_update_ha_state()
        except Exception as error:  # pylint: disable=broad-except
            LOGGER.debug("State update failed.")

    @property
    def name(self):
        """Return the name of the entity."""
        return self._name

    @property
    def icon(self):
        return "mdi:speaker"

    @property
    def should_poll(self) -> bool:
        """Return True if entity has to be polled for state.
        False if entity pushes its state to HA.
        """
        return False

    @property
    def state(self) -> MediaPlayerState | None:
        """Return the state of the device."""
        if self._isOn:
            return MediaPlayerState.ON
        else:
            return MediaPlayerState.OFF

    @property
    def available(self) -> bool:
        """Return if the media player is available."""
        return True

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        """Flag media player features that are supported."""
        return MediaPlayerEntityFeature.TURN_ON | MediaPlayerEntityFeature.TURN_OFF

    """ @property
    def source(self) -> str | None:
        # Return the current input source.
        return self._sourceName

    @property
    def source_list(self):
        # List of available input sources.
        return self._controller.clean_inputs

    async def async_select_source(self, source):
        # Select input source.
        index = self._controller.video_inputs.index(source)+1
        await self._controller.async_send(f"SET OUT{self._index} {self._output_type}S IN{index}")

    async def async_turn_on(self):
        # Turn the media player on.
        await self._controller.async_send(f"SET OUT{self._index} STREAM ON")

        # Reset our input signal please
        if self._sourceIndex != -1:
            await self._controller.async_send(f"A00 SET IN{self._sourceIndex} RST")

    async def async_turn_off(self):
        await self._controller.async_send(f"SET OUT{self._index} STREAM OFF")

    @property
    def extra_state_attributes(self):
        # Return extra state attributes
        if self._isOn:
            self._extra_attributes['input_index']=self._sourceIndex+1
            self._extra_attributes['input_has_signal']= (self._controller._inputSignals[self._sourceIndex]==1)
        else:
            self._extra_attributes['input_index']=0
            self._extra_attributes['input_has_signal']= False

        # Useful for making sensors
        return self._extra_attributes """