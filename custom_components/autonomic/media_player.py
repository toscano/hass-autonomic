"""Platform for media_player integration."""

import logging

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
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
from homeassistant.helpers.entity_registry import RegistryEntryHider
import homeassistant.helpers.entity_registry as er

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

        # Member variables that will never need to change
        self._hass = hass
        self._controller = controller
        self._attr_device_class = MediaPlayerDeviceClass.SPEAKER

        # Member variables that will change as things go...

        # from MMS
        self._mms_groupGuid = ""
        self._mms_groupName = ""

        # from HASS
        self._attr_app_name = ""
        self._extra_attributes = {}
        self._isOn = False

        """
        self._attr_app_id: str | None = None
        self._attr_app_name: str | None = None
        self._attr_device_class: MediaPlayerDeviceClass | None
        self._attr_group_members: list[str] | None = None
        self._attr_is_volume_muted: bool | None = None
        self._attr_media_album_artist: str | None = None
        self._attr_media_album_name: str | None = None
        self._attr_media_artist: str | None = None
        self._attr_media_channel: str | None = None
        self._attr_media_content_id: str | None = None
        self._attr_media_content_type: MediaType | str | None = None
        self._attr_media_duration: int | None = None
        self._attr_media_episode: str | None = None
        self._attr_media_image_hash: str | None
        self._attr_media_image_remotely_accessible: bool = False
        self._attr_media_image_url: str | None = None
        self._attr_media_playlist: str | None = None
        self._attr_media_position_updated_at: dt.datetime | None = None
        self._attr_media_position: int | None = None
        self._attr_media_season: str | None = None
        self._attr_media_series_title: str | None = None
        self._attr_media_title: str | None = None
        self._attr_media_track: int | None = None
        self._attr_repeat: RepeatMode | str | None = None
        self._attr_shuffle: bool | None = None
        self._attr_sound_mode_list: list[str] | None = None
        self._attr_sound_mode: str | None = None
        self._attr_source_list: list[str] | None = None
        self._attr_source: str | None = None
        self._attr_state: MediaPlayerState | None = None
        self._attr_supported_features: MediaPlayerEntityFeature = MediaPlayerEntityFeature(0)
        self._attr_volume_level: float | None = None
        self._attr_volume_step: float
        """

        if controller._mode == MODE_MRAD:
            self._mms_zone_id = f"Zone_{int(indexOrName)}"
            self._mms_source_id = ""
            self._name = f"{controller._name} Zone {int(indexOrName):02d}"
            self._attr_unique_id = f"{entry.unique_id}_zone_{int(indexOrName):02d}"

        elif controller._mode == MODE_STANDALONE:
            self._mms_zone_id = None
            self._mms_source_id = f"{indexOrName}".replace(' ', '_')
            self._name = f"{controller._name} {indexOrName}"
            self._attr_unique_id = f"{entry.unique_id}_{indexOrName}"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id)},
            manufacturer=MANUFACTURER,
            model=self._controller._name,
            name=self._name
        )

        controller.add_zone_entity(self)


    def update_ha(self):
        try:
            self.schedule_update_ha_state()
        except Exception as error:  # pylint: disable=broad-except
            LOGGER.debug("State update failed.")

    def set_name_source_and_group(self, newName: str | None = None, newSourceId: str | None = None, newGroupGuid: str | None = None, newGroupName: str | None = None):

        isDirty = False

        if newName is not None and newName != self._name:
            if self._mms_zone_id is not None and self._mms_zone_id==newName.replace(' ', '_'):
                LOGGER.debug(f"Attempt to hide {newName}")
                entity_registry = er.async_get(self._hass)
                self.registry_entry = entity_registry.async_update_entity(self.entity_id, hidden_by = RegistryEntryHider.INTEGRATION )

            self._name = newName
            isDirty = True


        if newSourceId is not None and newSourceId != self._mms_source_id:
            self._mms_source_id = newSourceId
            isDirty = True

        if newGroupGuid is not None:
            self._mms_groupGuid = newGroupGuid

        if newGroupName is not None:
            self._mms_groupName = newGroupName

        if isDirty:
            self.schedule_update_ha_state()


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

    def GetSourceEvent(self, event_id : str ) -> str | None:

        if self._controller.is_connected:
            sourceId = self._mms_source_id
            if sourceId is None:
                return None

            val = self._controller.get_event(sourceId, event_id)

            if val is not None:
                return val

            if self._controller._mode == MODE_MRAD:
                val = self._controller.get_event(sourceId, 'QualifiedSourceName')

                if val is None:
                    return None

                sourceId = val

            return self._controller.get_event(sourceId, event_id)

        return None

    @property
    def app_name(self) -> str | None:
        #Name of the current running app.
        x = self._attr_app_name = self.GetSourceEvent("MetaData1") # NowPlayingSrceName
        return self._attr_app_name


    @property
    def available(self) -> bool:
        self._attr_available = self._controller.is_connected
        return self._controller.is_connected

    @property
    def state(self) -> MediaPlayerState | None:

        if self._controller.is_connected:

            self._isOn = False
            self._attr_state = MediaPlayerState.OFF

            if self._controller._mode == MODE_MRAD:
                power = self._controller.get_event(self._mms_zone_id, 'PowerOn')
            else:
                power = 'True' # since MODE_STANDALONE zones (aka instances) are ALWAYS ON

            if power is None:
                return self._attr_state

            elif power.find('T')==0:
                self._isOn = True
                self._attr_state = MediaPlayerState.ON

                mediaControl = self.GetSourceEvent('MediaControl')

                if mediaControl is not None:
                    if mediaControl == 'Pause':
                        self._attr_state = MediaPlayerState.PAUSED
                    elif mediaControl == 'Stop':
                        self._attr_state = MediaPlayerState.IDLE
                    elif mediaControl == 'Play':
                        self._attr_state = MediaPlayerState.PLAYING

        return self._attr_state


    @property
    def available(self) -> bool:
        """Return if the media player is available."""
        return True

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        """Flag media player features that are supported."""
        return MediaPlayerEntityFeature.TURN_ON | MediaPlayerEntityFeature.TURN_OFF | MediaPlayerEntityFeature.GROUPING | MediaPlayerEntityFeature.SELECT_SOURCE

    @property
    def source(self) -> str | None:
        # Name of the current input source.
        sourceName = None

        if self._controller.is_connected:
            sourceName = self._controller.get_event(self._mms_source_id, 'QualifiedSourceName')

            if sourceName is not None and sourceName == "":
                sourceName = None

            if sourceName is not None:
                sourceName = sourceName.split("@")[0]

            if sourceName is None:
                sourceName = self._controller.get_event(self._mms_source_id, 'SourceName')

            if sourceName is not None and sourceName == "":
                sourceName = None

        return sourceName


    @property
    def source_list(self) -> list[str] | None:
        # From ZoneGroups
        # List of available input sources.
        sourceList = None

        if self._controller.is_connected:
            sourceList = self._controller.get_event(self._mms_zone_id, 'SourceList')

        return sourceList


    """
    async def async_select_source(self, source):
        # Select input source.
        index = self._controller.video_inputs.index(source)+1
        await self._controller.async_send(f"SET OUT{self._index} {self._output_type}S IN{index}")
    """
    async def async_turn_on(self):
        # Turn the media player on.
        self._name = self._name + " Kitchen"
        self._isOn = True
        self.update_ha()

    async def async_turn_off(self):
        indx = self._name.find(" Kitchen")

        if indx > 0:
            self._name = self._name[0:indx]

        self._isOn = False
        self.update_ha()

    """
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

    @property
    def media_title(self) -> str | None:
        # Title of current playing media.
        self._attr_media_title = self.GetSourceEvent("MetaData4")
        return self._attr_media_title


    @property
    def media_artist(self):
        # Artist of current playing media, music track only.
        self._attr_media_artist = self.GetSourceEvent("MetaData2")
        return self._attr_media_artist

    @property
    def media_album_name(self):
        # Album name of current playing media, music track only.
        self._attr_media_artist = self.GetSourceEvent("MetaData3")
        return self._attr_media_album_name

    @property
    def media_image_url(self) -> str | None:
        # From ZoneGroups
        # Image url of current playing media.
        self._attr_media_image_url = self.GetSourceEvent("mArt")
        return self._attr_media_image_url
