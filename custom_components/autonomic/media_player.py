"""Platform for media_player integration."""

import logging

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    RepeatMode,
    MediaType,

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
            else:
                LOGGER.debug(f"Changing name from {self._name} to {newName}.")
            self._name = newName
            isDirty = True


        if newSourceId is not None and newSourceId != self._mms_source_id:
            LOGGER.debug(f"Changing source from {self._mms_source_id} to {newSourceId}.")
            self._mms_source_id = newSourceId
            isDirty = True

        if newGroupGuid is not None:
            self._mms_groupGuid = newGroupGuid

        if newGroupName is not None:
            self._mms_groupName = newGroupName

        if isDirty:
            self.update_ha()

    def GetSourceEvent(self, event_id : str ) -> str | None:
        debug = False #event_id in ['TrackDuration', 'TrackTime']

        if self._controller.is_connected:
            if debug:
                LOGGER.debug(f"{self._mms_zone_id}:{self._mms_source_id} looking for {event_id}:")

            sourceId = self._mms_source_id
            if sourceId is None:
                return None

            val = self._controller.get_event(sourceId, event_id)
            if debug:
                LOGGER.debug(f"v1: get_event({sourceId},{event_id} == {val}")

            if val is not None:
                return val

            if self._controller._mode == MODE_MRAD:
                val = self._controller.get_event(sourceId, 'QualifiedSourceName')
                if debug:
                    LOGGER.debug(f"v2: get_event({sourceId},'QualifiedSourceName' == {val}")

                if val is not None:
                    val = val.split("@")[0]

                if debug:
                    LOGGER.debug(f"v3: get_event({sourceId},'QualifiedSourceName') == {val}")

                if val is None:
                    return None

                sourceId = val

            rVal = self._controller.get_event(sourceId, event_id)
            if debug:
                LOGGER.debug(f"v4: get_event({sourceId},{event_id}) == {rVal}")

            return rVal

        return None

    # ==== HASS PROPERTIES ======================================================================================================

    @property
    def name(self):
        """Return the name of the entity."""
        return self._name

    @property
    def icon(self):
        # Our ICON
        power = False
        if self._controller._mode == MODE_MRAD:
            powerOn = self._controller.get_event(self._mms_zone_id, 'PowerOn')
            power = (powerOn is not None and powerOn.find('T')==0)
        else:
            power = True # since MODE_STANDALONE zones (aka instances) are ALWAYS ON

        if power:
            return "mdi:speaker"

        return "mdi:speaker-off"

    @property
    def should_poll(self) -> bool:
        """Return True if entity has to be polled for state.
        False if entity pushes its state to HA.
        """
        return False

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
        # Flag media player features that are supported.
        s: MediaPlayerEntityFeature = MediaPlayerEntityFeature(0)

        if self._controller.is_connected:

            smartSource = self.GetSourceEvent('SmartSource')

            if smartSource is None:
                smartSource = False

            s = 0

            if smartSource:

                s = MediaPlayerEntityFeature.VOLUME_STEP     | \
                    MediaPlayerEntityFeature.VOLUME_SET      | \
                    MediaPlayerEntityFeature.VOLUME_MUTE     | \
                    MediaPlayerEntityFeature.TURN_ON         | \
                    MediaPlayerEntityFeature.TURN_OFF        | \
                    MediaPlayerEntityFeature.PLAY_MEDIA      | \
                    MediaPlayerEntityFeature.SELECT_SOURCE   | \
                    MediaPlayerEntityFeature.PAUSE           | \
                    MediaPlayerEntityFeature.STOP            | \
                    MediaPlayerEntityFeature.CLEAR_PLAYLIST  | \
                    MediaPlayerEntityFeature.PLAY

                #ReportState Player_A SkipNextAvailable=True
                b = self.GetSourceEvent('SkipNextAvailable')
                if b is not None and b.find('T')==0:
                    s = s | MediaPlayerEntityFeature.NEXT_TRACK

                #ReportState Player_A SkipPrevAvailable=True
                b = self.GetSourceEvent('SkipPrevAvailable')
                if b is not None and b.find('T')==0:
                    s = s | MediaPlayerEntityFeature.PREVIOUS_TRACK

                #ReportState Player_A ShuffleAvailable=True
                b = self.GetSourceEvent('ShuffleAvailable')
                if b is not None and b.find('T')==0:
                    s = s |  MediaPlayerEntityFeature.SHUFFLE_SET

                #ReportState Player_A SeekAvailable=True
                b = self.GetSourceEvent('SeekAvailable')
                if b is not None and b.find('T')==0:
                    s = s |  MediaPlayerEntityFeature.SEEK

                #ReportState Player_A RepeatAvailable=True
                b = self.GetSourceEvent('RepeatAvailable')
                if b is not None and b.find('T')==0:
                    s = s |  MediaPlayerEntityFeature.REPEAT_SET



                #ReportState Player_A PlayPauseAvailable=True

            else:

                s = MediaPlayerEntityFeature.VOLUME_STEP     | \
                    MediaPlayerEntityFeature.VOLUME_SET      | \
                    MediaPlayerEntityFeature.VOLUME_MUTE     | \
                    MediaPlayerEntityFeature.TURN_ON         | \
                    MediaPlayerEntityFeature.TURN_OFF        | \
                    MediaPlayerEntityFeature.PLAY_MEDIA      | \
                    MediaPlayerEntityFeature.SELECT_SOURCE   | \
                    MediaPlayerEntityFeature.CLEAR_PLAYLIST

            if self._controller._mode == MODE_STANDALONE:
                s = s & ~MediaPlayerEntityFeature.TURN_ON & ~MediaPlayerEntityFeature.TURN_OFF & ~MediaPlayerEntityFeature.SELECT_SOURCE

                gainMode = self.GetSourceEvent('GainMode')
                if gainMode is not None and gainMode == 'Fixed':
                    s = s & ~MediaPlayerEntityFeature.VOLUME_SET & ~MediaPlayerEntityFeature.VOLUME_STEP

        self._attr_supported_feature = s
        return s


    @property
    def source(self) -> str | None:
        # Name of the current input source.
        sourceName = None

        if self._controller.is_connected:
            sourceName = self._controller.get_event(self._mms_source_id, 'QualifiedSourceName')

            if sourceName is not None and sourceName == "":
                sourceName = None

            if sourceName is None:
                sourceName = self._controller.get_event(self._mms_source_id, 'SourceName')

            if sourceName is not None:
                sourceName = sourceName.split("@")[0].replace('_', ' ')

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
    def media_content_type(self) -> MediaType | str | None:
        # Content type of current playing media.
        mediaControl = self.GetSourceEvent('MediaControl')

        if mediaControl is None or mediaControl == 'Stop':
            _attr_media_content_type = None
        else:
            _attr_media_content_type = MediaType.MUSIC

        return _attr_media_content_type

    @property
    def app_name(self) -> str | None:
        #Name of the current running app.
        x = self._attr_app_name = self.GetSourceEvent("MetaData1") # NowPlayingSrceName
        return self._attr_app_name

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

    @property
    def media_duration(self) -> int | None:
        # Duration of current playing media in seconds.
        _attr_media_duration = None

        duration = self.GetSourceEvent('TrackDuration')

        if duration is not None:
            duration = duration.replace("00:00:00", "0")

            if int(duration) > 0:
                _attr_media_duration = int(duration)

        return _attr_media_duration

    @property
    def media_position(self):
        # Position of current playing media in seconds.
        if self._controller.is_connected:
            position = self.GetSourceEvent('TrackTime')

            if position is None:
                return None

            position = position.replace("00:00:00", "0")

            if int(position)==0:
                return None

            self._attr_media_position = int(position)

            return self._attr_media_position

        return None


    @property
    def media_position_updated_at(self):
        # When was the position of the current playing media valid.
        # Returns value from homeassistant.util.dt.utcnow().
        position_utc = self.GetSourceEvent('TrackTimeUtc')

        if position_utc is None:
            return None

        self._attr_media_position_updated_at = position_utc

        return self._attr_media_position_updated_at

    @property
    def repeat(self) -> RepeatMode | str | None:
        # Return current repeat mode.
        r = self.GetSourceEvent('Repeat')

        if r is None:
            self._attr_repeat = None
        elif r.find('T')==0:
            self._attr_repeat = RepeatMode.ALL
        else:
            self._attr_repeat = RepeatMode.OFF

        return self._attr_repeat

    @property
    def shuffle(self) -> bool | None:
        # Return current repeat mode.
        r = self.GetSourceEvent('Shuffle')

        if r is None:
            self._attr_shuffle = None
        elif r.find('T')==0:
            self._attr_shuffle = True
        else:
            self._attr_shuffle = False

        return self._attr_shuffle


    @property
    def is_volume_muted(self) -> bool | None:
        # Boolean if volume is currently muted.
        self._attr_is_volume_muted = None

        if self._controller.is_connected:

            if self._controller._mode == MODE_MRAD:
                mute = self._controller.get_event(self._mms_zone_id, 'Mute')
            else:
                mute = self._controller.get_event(self._mms_source_id, 'Mute')

            if mute is not None:
                if mute.find('T')==0:
                    self._attr_is_volume_muted = True
                else:
                    self._attr_is_volume_muted = False

        return self._attr_is_volume_muted

    @property
    def volume_level(self) -> float | None:
        # Volume level of the media player (0..1).
        self._attr_volume_level = None

        if self._controller.is_connected:

            if self._controller._mode == MODE_MRAD:
                maxVolume = self._controller.get_event(self._mms_zone_id, 'MaxVolume')
                volume = self._controller.get_event(self._mms_zone_id, 'Volume')
            else:
                maxVolume = 50
                gainMode = self._controller.get_event(self._mms_source_id, 'GainMode')
                if gainMode is None:
                    volume = 50
                elif gainMode == 'Fixed':
                    volume = 50
                else:
                    volume = self._controller.get_event(self._mms_source_id, 'Volume')


            if maxVolume is None:
                maxVolume = 80
            elif float(maxVolume) == 0:
                maxVolume = 80

            if volume is None:
                volume = 0

            self._attr_volume_level = float(volume) / float(maxVolume)

        return self._attr_volume_level


    # === HASS METHODS ==========================================================================================================

    def mute_volume(self, mute) -> None:
        # Mute the volume.
        if mute:
            newState = "on"
        else:
            newState = "off"

        if self._controller._mode == MODE_MRAD:
            self._controller.send(f'mrad.SetZone "{self._mms_zone_id}"')
            self._controller.send(f'mrad.mute {newState}')
        else:
            self._controller.send(f'setInstance "{self._mms_source_id}"')
            self._controller.send(f'mute {newState}')

    """
    async def async_select_source(self, source):
        # Select input source.
        index = self._controller.video_inputs.index(source)+1
        await self._controller.async_send(f"SET OUT{self._index} {self._output_type}S IN{index}")
    """

    def turn_on(self) -> None:
        # Turn the media player on.
        if self._controller._mode == MODE_MRAD:
            self._controller.send(f'mrad.power on "{self._mms_zone_id}"')

    def turn_off(self):
        # Turn the media player off.
        if self._controller._mode == MODE_MRAD:
            self._controller.send(f'mrad.power off "{self._mms_zone_id}"')

    def media_previous_track(self) -> None:
        # Send previous track command.
        if self._controller._mode == MODE_MRAD:
            self._controller.send(f'mrad.SetZone "{self._mms_zone_id}"')
            self._controller.send('mrad.SkipPrevious')
        else:
            self._controller.send(f'setInstance "{self._mms_source_id}"')
            self._controller.send('SkipPrevious')

    def media_next_track(self) -> None:
        # Send next track command.
        if self._controller._mode == MODE_MRAD:
            self._controller.send(f'mrad.SetZone "{self._mms_zone_id}"')
            self._controller.send('mrad.SkipNext')
        else:
            self._controller.send(f'setInstance "{self._mms_source_id}"')
            self._controller.send('SkipNext')

    def set_repeat(self, repeat: RepeatMode) -> None:
        # Set repeat mode.
        if repeat == RepeatMode.OFF or repeat == RepeatMode.ONE:
            arg = "False"
        else:
            arg = "True"

        if self._controller._mode == MODE_MRAD:
            self._controller.send(f'mrad.SetZone "{self._mms_zone_id}"')
            self._controller.send(f'mrad.Repeat {arg}')
        else:
            self._controller.send(f'setInstance "{self._mms_source_id}"')
            self._controller.send(f'Repeat {arg}')

    def set_shuffle(self, shuffle: bool) -> None:
        # Enable/disable shuffle mode.

        if self._controller._mode == MODE_MRAD:
            self._controller.send(f'mrad.SetZone "{self._mms_zone_id}"')
            self._controller.send(f'mrad.Shuffle {shuffle}')
        else:
            self._controller.send(f'setInstance "{self._mms_source_id}"')
            self._controller.send(f'Shuffle {shuffle}')

    def set_volume_level(self, volume: float) -> None:
        # Set volume level, range 0..1.
        if self._controller._mode == MODE_MRAD:
            maxVolume = self._controller.get_event(self._mms_zone_id, 'MaxVolume')

            if maxVolume is None:
                maxVolume = 80

            volume = int( float(volume) * float(maxVolume) )

            self._controller.send(f'mrad.SetZone "{self._mms_zone_id}"')
            self._controller.send(f'mrad.volume {volume}')
        else:
            gainMode = self._controller.get_event(self._mms_zone_id, 'GainMode')
            if gainMode is not None and gainMode == 'Fixed':
                return

            maxVolume = 50

            volume = int( float(volume) * float(maxVolume) )
            self._controller.send(f'setInstance "{self._mms_source_id}"')
            self._controller.send(f'SetVolume {volume}')

    async def async_volume_up(self) -> None:
        """Volume up the media player."""
        if self._controller._mode == MODE_MRAD:
            self._controller.send(f'mrad.SetZone "{self._mms_zone_id}"')
            self._controller.send('mrad.VolumeUp')
        else:
            gainMode = self._controller.get_event(self._mms_zone_id, 'GainMode')
            if gainMode is not None and gainMode == 'Fixed':
                return

            self._controller.send(f'setInstance "{self._mms_source_id}"')
            self._controller.send('VolumeUp')

    async def async_volume_down(self) -> None:
        """Volume down the media player."""
        if self._controller._mode == MODE_MRAD:
            self._controller.send(f'mrad.SetZone "{self._mms_zone_id}"')
            self._controller.send('mrad.VolumeDown')
        else:
            gainMode = self._controller.get_event(self._mms_zone_id, 'GainMode')
            if gainMode is not None and gainMode == 'Fixed':
                return

            self._controller.send(f'setInstance "{self._mms_source_id}"')
            self._controller.send('VolumeDown')

