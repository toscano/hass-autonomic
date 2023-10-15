"""
Support for Autonomic e-Series devices.
For more information visit
https://github.com/toscano/hass-autonomic
"""
import asyncio

import sys
from distutils.version import LooseVersion

from datetime import timedelta
import logging

import aiohttp
from aiohttp.client_exceptions import ClientError
from aiohttp.hdrs import CONNECTION, KEEP_ALIVE
import async_timeout
import voluptuous as vol
import xmltodict

from homeassistant.components import media_source, spotify

from homeassistant.components.media_player import (
    ATTR_TO_PROPERTY,
    DOMAIN,
    PLATFORM_SCHEMA,
    MediaPlayerEntity,
    async_process_play_media_url
)
from homeassistant.components.media_player.const import (
    MEDIA_TYPE_MUSIC,
    MediaPlayerEntityFeature
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_HOST,
    CONF_HOSTS,
    CONF_MODE,
    CONF_PORT,
    EVENT_HOMEASSISTANT_START,
    EVENT_HOMEASSISTANT_STOP,
    STATE_IDLE,
    STATE_PAUSED,
    STATE_OFF,
    STATE_ON,
    STATE_PLAYING,
    STATE_UNKNOWN
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import Throttle
import homeassistant.util.dt as dt_util

REQUIREMENTS = ['xmltodict==0.12.0']

_LOGGER = logging.getLogger(__name__)


DATA_AUTONOMIC          = 'autonomic'
MODE_MRAD               = 'mrad'
MODE_STANDALONE         = 'standalone'
DEFAULT_MODE            = MODE_MRAD
DEFAULT_PORT            = 5004
MIN_VERSION_REQUIRED    =  '6.1.20180215.0'
RETRY_CONNECT_SECONDS   = 30
RETRY_WRONG_VERSION_SEC = 10 * 60
STATE_OFFLINE           = 'offline'

# Service Call validation schemas
SERVICE_ALL_OFF         = 'autonomic_all_off'
ATTR_SYSTEM_ID          = 'autonomic_system_id'
ATTR_PLATFORM           = 'platform'
ATTR_VERSION            = 'autonomic_version'
ATTR_MODE               = 'mode'

TICK_THRESHOLD_SECONDS  = 5
TICK_UPDATE_SECONDS     = 30

PING_INTERVAL           = timedelta(seconds=10)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_HOSTS): vol.All(cv.ensure_list, [{
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
        vol.Optional(CONF_MODE, default=DEFAULT_MODE): cv.string,
    }])
})

AUTONOMIC_SCHEMA = vol.Schema({
    vol.Optional(ATTR_ENTITY_ID): cv.entity_ids,
})

SERVICE_TO_METHOD = {
    SERVICE_ALL_OFF: {
        'method': 'async_all_off',
        'schema': None }
}



def _add_autonomic_host(hass, async_add_devices, host, port=None, mode=None, fromConfig=False):

    """Add Autonomic host."""
    if host in [x.host for x in hass.data[DATA_AUTONOMIC]]:
        if fromConfig:
            _LOGGER.warn("Check configuration, Duplicate Autonomic host: %s", host)
        return

    @callback
    def _init_streamer(event=None):
        """Try to initialize communications with this streamer."""
        hass.async_add_job(streamer.async_init())

    @callback
    def _start(event=None):
        """Start communicating with this host."""
        streamer.start()

    @callback
    def _shutting_down(notUsed):
        """Stop communincating with this host."""
        hass.async_add_job(streamer.async_stop())

    @callback
    def _init_complete_cb():

        if hass.is_running:
            _LOGGER.debug("Starting %s", streamer.host)
            _start()
        else:
            _LOGGER.debug("Will start %s", streamer.host)
            hass.bus.async_listen_once( EVENT_HOMEASSISTANT_START, _start)

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _shutting_down)
    streamer = AutonomicStreamer(hass, host, port, mode, async_add_devices, _init_complete_cb)
    hass.data[DATA_AUTONOMIC].append(streamer)

    if hass.is_running:
        _init_streamer()
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_START, _init_streamer)


async def async_setup_platform(hass, config, async_add_devices, discovery_info=None):
    """Set up the Autonomic platforms."""
    if DATA_AUTONOMIC not in hass.data:
        hass.data[DATA_AUTONOMIC] = []

    if discovery_info:
        _add_autonomic_host( hass
                            , async_add_devices
                            , discovery_info.get(CONF_HOST)
                            , discovery_info.get(CONF_PORT, None)
                            , False)
        return

    hosts = config.get(CONF_HOSTS, None)
    if hosts:
        for host in hosts:
            _add_autonomic_host( hass
                                , async_add_devices
                                , host.get(CONF_HOST)
                                , host.get(CONF_PORT)
                                , host.get(CONF_MODE).lower()
                                , True)

    async def async_service_handler(service):
        method = SERVICE_TO_METHOD.get(service.service)
        if not method:
            return

        _LOGGER.info("Service call: %s", service.service)

        if service.service == SERVICE_ALL_OFF:
            for streamer in hass.data[DATA_AUTONOMIC]:
                streamer.send('mrad.alloff')
            return

    for service in SERVICE_TO_METHOD:
        schema = SERVICE_TO_METHOD[service]['schema']
        hass.services.async_register( DOMAIN, service, async_service_handler, schema=schema)







class AutonomicStreamer:
    def __init__(self, hass, host, port=None, mode=None, async_add_devices=None, init_callback=None):
        """Initialize the data channel to this device"""
        self._hass = hass
        self.host = host
        self._port = port
        self._mode = mode
        self._name = None
        self.is_connected = False

        self._async_add_devices = async_add_devices
        self._init_callback = init_callback
        self._cmd_queue = None
        self._ioloop_future = None
        self._closing = False
        self._queue_future = None
        self._net_future = None
        self._zones = {}
        self._events = {}
        self._last_inbound_data_utc = dt_util.utcnow()
        self._sent_ping = 0

        async_track_time_interval(self._hass, self._async_check_ping, PING_INTERVAL)

    async def async_init(self):
        self._init_callback()

    def start(self):
        """Let's connect and get some info from this server..."""
        _LOGGER.debug("%s:start %s with mode=%s", self.host, self.host, self._mode)
        self._hass.async_add_job(self._async_open())

    async def async_stop(self):
        """Stop - Like a destructor."""
        _LOGGER.debug("%s:stop %s", self.host, self.host)
        self._closing = True
        self._queue_future.cancel()
        self.is_connected = False

    async def _async_open(self):
        """
        Connect to the server and start processing responses.
        """
        self.is_connected = False
        self._last_inbound_data_utc = dt_util.utcnow()
        self._sent_ping = 0

        # If we have any Zones... get them to update their state to OFFLINE
        for guid in self._zones:
            zone = self._zones[guid]
            zone.schedule_update_ha_state()

        # Let's Make a http request to the upnp description to get the id and friendly name...
        workToDo = True
        while workToDo:
            connectHoldoff = RETRY_CONNECT_SECONDS
            try:
                websession = async_get_clientsession(self._hass)
                url = "http://{}:5005/upnp/DevDesc/0.xml".format(self.host)
                with async_timeout.timeout(10):
                        response = await websession.get(url)

                body = await response.text()
                #_LOGGER.debug(body)

                data = xmltodict.parse(body)

                # Use the License GUID as the unique id for this streamer
                # or, if you can't find it, use the upnp UDN.
                idx = body.find("<!-- LID:")
                if idx >= 0:
                    self.id = body[idx+9:idx+9+8]
                else:
                    self.id = data['root']['device']['UDN'][5:5+8]

                self.name = data['root']['device']['friendlyName']

                # Min version check if not running Debug bits
                self.version = data['root']['device']['modelNumber']
                version = self.version
                idx = version.find('Debug')
                if idx < 0:
                    idx = version.find(' ')
                    if idx >= 0:
                        version = version[:idx]

                    if  LooseVersion(version) < LooseVersion(MIN_VERSION_REQUIRED):
                        _LOGGER.error("%s:Server at %s is running %s. Min required is %s.", self.id, self.host, version, MIN_VERSION_REQUIRED)
                        connectHoldoff = RETRY_WRONG_VERSION_SEC
                        raise ValueError

                workToDo = False
            except:
                _LOGGER.warn("%s:Description request to %s failed... will try again in %d seconds.", self.host, url, connectHoldoff)
                await asyncio.sleep(connectHoldoff)

        # Now open the socket
        workToDo = True
        while workToDo:
            try:
                _LOGGER.info("%s:Connecting to %s:%s", self.id, self.host, self._port)

                reader, writer = await asyncio.open_connection(self.host, self._port)
                workToDo = False
            except:
                _LOGGER.warn("%s:Connection to %s:%s failed... will try again in %d seconds.", self.id, self.host, self._port, RETRY_CONNECT_SECONDS)
                await asyncio.sleep(RETRY_CONNECT_SECONDS)

        # reset the pending commands
        self._cmd_queue = asyncio.Queue()

        self._events = {}

        # Get the Zone structure
        self.send('setclienttype hass')
        self.send('setxmlmode lists')

        if self._mode == MODE_STANDALONE:
            self.send('browseinstances')

            # Subscribe and catchup
            self.send('subscribeevents')
            self.send('getstatus')
        else:
            self.send('mrad.browsezones')
            self.send('mrad.browsezonegroups')

            # Subscribe and catchup
            self.send('mrad.subscribeevents')
            self.send('mrad.getstatus')

        self._ioloop_future = asyncio.ensure_future(self._ioloop(reader, writer))

        _LOGGER.info("%s:Connected to %s:%s", self.id, self.host, self._port)
        self.is_connected = True

    async def _async_check_ping(self, now=None):
        """Maybe send a ping."""
        if (self.is_connected == False):
            return

        if (self._last_inbound_data_utc + PING_INTERVAL + PING_INTERVAL < dt_util.utcnow() ):

            if (self._sent_ping > 2):
                # Schedule a re-connect...
                _LOGGER.error("%s:PING...reconnect needed.", self.id)
                self._sent_ping = 0
                self.is_connected = False
                self._hass.async_add_job(self._async_open())
                return
            self._sent_ping = self._sent_ping + 1
            if (self._sent_ping > 1):
                _LOGGER.debug("%s:PING...sending ping %d", self.id, self._sent_ping)
            self.send("ping")
        elif (self._sent_ping > 0):
            if (self._sent_ping > 1):
                _LOGGER.debug("%s:PING...resetting ping %s",  self.id, self._last_inbound_data_utc)
            self._sent_ping = 0

    async def _async_close(self):
        """
        Disconnect from the server.
        """
        _LOGGER.info("%s:Closing connection to %s:%s", self.id, self.host, self._port)
        self._closing = True
        self._queue_future.cancel()

    async def _ioloop(self, reader, writer):

        self._queue_future = asyncio.ensure_future(self._cmd_queue.get())

        self._net_future = asyncio.ensure_future(reader.readline())

        try:

            while True:

                done, pending = await asyncio.wait(
                        [self._queue_future, self._net_future],
                        return_when=asyncio.FIRST_COMPLETED)

                if self._closing:
                    writer.close()
                    self._queue_future.cancel()
                    self._net_future.cancel()
                    _LOGGER.info("%s:IO loop exited for local close", self.id)
                    return

                if self._net_future in done:

                    if reader.at_eof():
                        self._queue_future.cancel()
                        self._net_future.cancel()
                        _LOGGER.info("%s:IO loop exited for remote close...", self.id)
                        return

                    response = self._net_future.result()
                    self._last_inbound_data_utc = dt_util.utcnow()
                    try:
                        self._process_response(response)
                    except:
                        pass

                    self._net_future = asyncio.ensure_future(reader.readline())

                if self._queue_future in done:
                    cmd = self._queue_future.result()
                    #_LOGGER.info("%s:--> %s", self.host, cmd)
                    cmd += '\r'
                    writer.write(bytearray(cmd, 'utf-8'))
                    await writer.drain()

                    self._queue_future = asyncio.ensure_future(self._cmd_queue.get())

            _LOGGER.debug("%s:IO loop exited", self.id)

        except GeneratorExit:
            return

        except asyncio.CancelledError:
            _LOGGER.debug("%s:IO loop cancelled", self.id)
            writer.close()
            self._queue_future.cancel()
            self._net_future.cancel()
            raise
        except:
            _LOGGER.exception("%s:Unhandled exception in IO loop",self.id)
            raise

    def send(self, cmd):
        _LOGGER.debug("%s:-->%s", self.id, cmd)
        self._cmd_queue.put_nowait(cmd)

    def _process_response(self, res):
        try:

            s = str(res, 'utf-8').strip()
            # _LOGGER.debug("%s:<--%s", self.host, s)

            if s.startswith('<Zones'):
                self._process_mrad_zone_response(s)
            elif s.startswith('<ZoneGroups'):
                self._process_mrad_zonegroup_response(s)
            elif s.startswith('MRAD.'):
                self._process_mrad_event(s)
            elif s.startswith('<Instances'):
                self._process_standalone_instance_response(s)
            elif s.startswith('ReportState') or s.startswith('StateChanged'):
                self._process_standalone_event(s)
            #else:
            #    _LOGGER.info("%s:unprocessed<--%s", self.host, s)

            return s

        except Exception as e:
            _LOGGER.exception("%s:_process_response ex with res=%s", self.host, res)
            # some error occurred, re-connect may fix that
            self.send('quit')

    def _process_standalone_instance_response(self, res):
        data = xmltodict.parse(res, force_list=('Instance',))

        if data['Instances']['@total'] == '0':
            _LOGGER.warn("%s:Total Instances=%s with mode=%s.", self.id, data['Instances']['@total'], self._mode)

        #  There's a chance that the Zone count is zero... That's handled as an exception/reconnect
        for instance in data['Instances']['Instance']:
            # <Instances total="1" start="1" more="false" art="false" alpha="false" displayAs="List">
            #    <Instance  name="Player_A"
            #               friendlyName="Player A"
            #               fqn="Player_A@D46A9160066E"
            #               dna="name"
            #               supports="MrledvpScbF"
            #               m1="Pandora: Diana Krall Radio"
            #               m2="Emilie-Claire Barlow"
            #               m3="Seule Ce Soir"
            #               m4="Seule Ce Soir"
            #               mArt="http://192.168.1.80:5005/GetArt?instance=Player_A@D46A9160066E&amp;ticks=638091505856194880&amp;guid={ab4bad9c-6f12-4a61-7466-85832dbc940c}"
            #               gainMode="Fixed" />
            # </Instances>
            guid    = instance['@fqn']
            sourceId= instance['@name']

            m1      = instance['@m1']
            self._events['{}.MetaData1'.format(sourceId)]=m1
            m2      = instance['@m2']
            self._events['{}.MetaData2'.format(sourceId)]=m2
            m3      = instance['@m3']
            self._events['{}.MetaData3'.format(sourceId)]=m3
            m4      = instance['@m4']
            self._events['{}.MetaData4'.format(sourceId)]=m4
            mArt    = instance['@mArt']
            self._events['{}.mArt'.format(sourceId) ]=mArt

            if guid in self._zones:
                found = self._zones[guid]
                found.schedule_update_ha_state()
            else:
                name    = instance['@friendlyName']
                id      = instance['@name']

                _LOGGER.info("%s:ADDING STANDALONE ZONE: %s %s", self.id, id, name)
                zone = AutonomicZone(self, self._hass, guid, name, id, sourceId, False)
                self._zones[guid] = zone
                self._async_add_devices([zone])

    def _process_standalone_event(self, res):
        # Parse...
        # StateChanged Player_A TrackTime=263
        splits = res.split(' ')
        nv = splits[2].split('=')
        name = nv[0]
        pEq = res.find('=')
        entityId = splits[1]

        key = '{}.{}'.format(entityId, name)
        value = res[pEq+1:]

        # Update our object for the first few TrackTime events
        # then only once every TICK_UPDATE_SECONDS
        if name == 'TrackTime':
            value = value.replace("00:00:00", "0")
            if key in self._events:
                if int(value) > TICK_THRESHOLD_SECONDS:
                    if int(value) % TICK_UPDATE_SECONDS != 0:
                        return

        self._events[key]=value

        # Manufacture TrackTimeUtc and since TrackTime
        # only occurs for SmartSources manufacture that too...
        if name == 'TrackTime':
            name = 'TrackTimeUtc'
            key = '{}.{}'.format(entityId, name)
            value = dt_util.utcnow()
            self._events[key]=value

            name = 'SmartSource'
            key = '{}.{}'.format(entityId, name)
            value = True
            self._events[key]=value

        # Shortcut to better art
        if name == 'MediaArtChanged':
            self.send('browseinstances')
            return

        # Schedule an update for the associated Zone(s)
        for guid in self._zones:
            zone = self._zones[guid]
            if zone._zoneId == entityId:
                zone.schedule_update_ha_state()
            elif zone._sourceId == entityId:
                zone.schedule_update_ha_state()



    def _process_mrad_zone_response(self, res):
        data = xmltodict.parse(res, force_list=('Zone',))

        if data['Zones']['@total'] == '0':
            _LOGGER.warn("%s:Total Zones=%s with mode=%s. Should you be using mode=%s ?", self.id, data['Zones']['@total'], self._mode, MODE_STANDALONE)

        #  There's a chance that the Zone count is zero... That's handled as an exception/reconnect
        for zone in data['Zones']['Zone']:
            # <Zones total="5" start="1" more="false" art="false" alpha="false" displayAs="List">
            #    <Zone guid="00000001-5ace-e5da-ba88-8cf58dd178f2"
            #          name="Office"
            #          dna="name"
            #          id="Zone_1"
            #          isOn="True"
            #          sourceId="20000"
            #          sourceName="Player A"
            #          gId="00000000-0000-4e20-0000-000000000000"
            #          gName="ZG_1"
            #          gPwr="1"
            #          gVol="0"
            #          gSrc="1"
            #          sId="20000"
            #          sGuid="11a7df11-bbb4-0586-4df2-b184f9ded057"
            #          m1="Pandora: Talking Heads Radio"
            #          m2="The Rolling Stones"
            #          m3="Hot Rocks (1964-1971) (Remastered)"
            #          m4="Honky Tonk Women"
            #          mArt=""
            #          iconId="Source" />
            guid    = zone['@guid']
            sourceId= 'Source_{}'.format(zone['@sourceId'])

            if guid in self._zones:
                found = self._zones[guid]
                found.set_source_and_group( sourceId, "", "" )
            else:
                name  = zone['@name']
                id    = zone['@id']

                # First add the standalone mrad zone
                _LOGGER.info("%s:ADDING MRAD ZONE: %s %s", self.id, id, name)
                zone = AutonomicZone(self, self._hass, guid, name, id, sourceId, False)
                self._zones[guid] = zone
                self._async_add_devices([zone])

                # Then add the Group mrad zone
                guid = guid + "_group"
                name = name + " group"
                _LOGGER.info("%s:ADDING MRAD GROUP ZONE: %s %s", self.id, id, name)
                zone = AutonomicZone(self, self._hass, guid, name, id, sourceId, True)
                self._zones[guid] = zone
                self._async_add_devices([zone])

    def _process_mrad_zonegroup_response(self, res):
        # This is a kludge that allows us to process <vol> and <src> zones as one element
        res = res.replace("</vol>", "")
        res = res.replace("<src>", "")
        res = res.replace("</src>", "</vol>")

        data = xmltodict.parse(res, force_list=('ZoneGroup',))

        for group in data['ZoneGroups']['ZoneGroup']:
            #<ZoneGroups total="3" start="1" more="false" art="false" alpha="false" displayAs="List" utcNow="2018-03-09T16:12:22Z" srceAvail="1" srceId="262c9674-9cb2-8860-e31a-0deefbddc26a" srceMmsAddr="192.168.1.80:5004" srceMmsInst="Player_B@0050C2FD2BF2">
            # <ZoneGroup guid="00000000-0000-4e20-0000-000000000000" name="ZG_1" dna="name" isSearchable="false" button="0" sId="20000" sGuid="11a7df11-bbb4-0586-4df2-b184f9ded057" m1="Pandora: Beck Radio" m2="Cake" m3="B-Sides And Rarities" m4="War Pigs" mArt="http://192.168.1.80:5005/GetArt?instance=Player_A@0050C2FD2BF2&amp;guid=ab4bad9c-6f12-4a61-7466-85832dbc940c&amp;ticks=636561900103465640" iconId="Source">
            #     <vol>
            #         <zone eventId="Zone_1" guid="00000001-5ace-e5da-ba88-8cf58dd178f2" name="MT Office" dna="name" icon="Zone" on="1" volume="32" mute="0" />
            #         <zone eventId="Zone_2" guid="00000002-5ace-e5da-ba88-8cf58dd178f2" name="MT Headphones" dna="name" icon="Zone" on="1" volume="30" mute="1" />
            #         <zone eventId="Zone_5" guid="00000005-85df-222c-1bf3-696cf573cf56" name="MT Rack I" dna="name" icon="Zone" on="1" volume="28" mute="0" />
            #         <zone eventId="Zone_6" guid="00000006-85df-222c-1bf3-696cf573cf56" name="MT Rack II" dna="name" icon="Zone" on="1" volume="30" mute="1" />
            #         <zone eventId="Zone_7" guid="00000007-85df-222c-1bf3-696cf573cf56" name="MT Rack III" dna="name" icon="Zone" on="1" volume="30" mute="1" />
            #         <zone eventId="Zone_8" guid="00000008-85df-222c-1bf3-696cf573cf56" name="MT Rack IV" dna="name" icon="Zone" on="1" volume="30" mute="1" />
            #     </vol>
            #     <src>
            #         <zone eventId="Zone_1" guid="00000001-5ace-e5da-ba88-8cf58dd178f2" name="MT Office" dna="name" icon="Zone" on="1" />
            #         <zone eventId="Zone_5" guid="00000005-85df-222c-1bf3-696cf573cf56" name="MT Rack I" dna="name" icon="Zone" on="1" />
            #     </src>
            #     <Sources>
            #         <Source guid="11a7df11-bbb4-0586-4df2-b184f9ded057" name="Player A" dna="name" isSearchable="false" fqn="Player_A@0050C2FD2BF2" smart="1" next="1" sId="20000" iconId="Source" />
            #         <Source guid="262c9674-9cb2-8860-e31a-0deefbddc26a" name="Player B" dna="name" isSearchable="false" fqn="Player_B@0050C2FD2BF2" smart="1" next="0" sId="20001" iconId="Source" />
            #         <Source guid="000027f5-5ace-e5da-ba88-8cf58dd178f2" name="CD120-1" dna="name" isSearchable="false" fqn="" smart="0" next="0" sId="10101" iconId="Source" />
            #     </Sources>
            # </ZoneGroup>
            groupGuid= group.get('@guid', "")
            groupName= group.get('@name', "")
            sId      = group.get('@sId', "0")
            sourceId = "Source_{}".format(sId)
            mArt     = group.get('@mArt', "" )

            if mArt == "":
                self._events['{}.mArt'.format(sourceId) ]=None
                self._events['{}.MetaData1'.format(sourceId) ]=None
                self._events['{}.MetaData2'.format(sourceId) ]=None
                self._events['{}.MetaData3'.format(sourceId) ]=None
                self._events['{}.MetaData4'.format(sourceId) ]=None
                self._events['{}.TrackDuration'.format(sourceId) ]=None
                self._events['{}.TrackTime'.format(sourceId) ]=None
                self._events['{}.TrackTimeUtc'.format(sourceId) ]=None
                self._events['{}.Shuffle'.format(sourceId) ]=None
                self._events['{}.SmartSource'.format(sourceId) ]=False
                self._events['{}.MediaControl'.format(sourceId) ]='Unknown'
            else:
                self._events['{}.mArt'.format(sourceId) ]=mArt
                self._events['{}.SmartSource'.format(sourceId) ]=True

            sources = []
            for source in group['Sources']['Source']:
                fqn = source.get('@name', "")
                if fqn == "":
                    fqn = source['@fqn'].split("@")[0]

                # Add that to the list of ALL sources for this (these) zone(s)
                sources.append(fqn)

                # And make sure that's correct in the event table
                sid = source.get('@sId', "")
                key = 'Source_{}.QualifiedSourceName'.format(sid)
                self._events[key] = fqn

            # Now set the available sources into the zone (zones)
            for vZone in group['vol']['zone']:
                eventId = vZone['@eventId']
                key = '{0}.SourceList'.format(eventId)
                self._events[key]=sources

                # Ensure that the sourceId is set correctly for the zone
                guid = vZone["@guid"]
                if guid in self._zones:
                    found = self._zones[guid]
                    found.set_source_and_group( sourceId, groupGuid, groupName )

                # And in the the zone group
                guid = guid + "_group"
                if guid in self._zones:
                    found = self._zones[guid]
                    found.set_source_and_group( sourceId, groupGuid, groupName )

    def _process_mrad_event(self, res):
        # Parse...
        # MRAD.ReportState Zone_1 ZoneGain=0
        splits = res.split(' ')
        nv = splits[2].split('=')
        name = nv[0]
        pEq = res.find('=')
        entityId = splits[1]

        key = '{}.{}'.format(entityId, name)
        value = res[pEq+1:]

        # Update our object for the first few TrackTime events
        # then only once every TICK_UPDATE_SECONDS
        if name == 'TrackTime':
            value = value.replace("00:00:00", "0")
            if key in self._events:
                if int(value) > TICK_THRESHOLD_SECONDS:
                    if int(value) % TICK_UPDATE_SECONDS != 0:
                        return

        self._events[key]=value

        # Manufacture TrackTimeUtc and since TrackTime
        # only occurs for SmartSources manufacture that too...
        if name == 'TrackTime':
            name = 'TrackTimeUtc'
            key = '{}.{}'.format(entityId, name)
            value = dt_util.utcnow()
            self._events[key]=value

            name = 'SmartSource'
            key = '{}.{}'.format(entityId, name)
            value = True
            self._events[key]=value

        # Schedule an update for the associated Zone(s)
        for guid in self._zones:
            zone = self._zones[guid]
            if zone._zoneId == entityId:
                zone.schedule_update_ha_state()
            elif zone._sourceId == entityId:
                zone.schedule_update_ha_state()

    def get_event(self, entityId, eventName):
        key = '{}.{}'.format(entityId, eventName)
        if key not in self._events:
            return None
        else:
            return self._events[key]

    def pop_event(self, entityId, eventName):
        key = '{}.{}'.format(entityId, eventName)
        return self._events.pop(key, None)

class AutonomicZone(MediaPlayerEntity):
    # Representation of an Autonomic Zone

    def __init__(self, parent, hass, guid, name, zoneId, sourceId, isGroupZone):
        self._parent        = parent
        self._hass          = hass
        self._guid          = guid
        self._name          = name
        self._zoneId        = zoneId
        self._sourceId      = sourceId
        self._isGroupZone   = isGroupZone
        self._groupGuid     = ""
        self._groupName     = ""

        self._access_token  = None

    def set_source_and_group(self, newSourceId, groupGuid, groupName):
        oldSourceId = self._sourceId

        if newSourceId != oldSourceId:
            self._sourceId = newSourceId
            self.schedule_update_ha_state()

        if groupGuid != "":
            self._groupGuid = groupGuid
            self._groupName = groupName

    @property
    def name(self):
        """Return the name of the entity."""
        return self._name

    @property
    def icon(self):
        if self.state == STATE_OFF:
            return "mdi:speaker-off"

        return "mdi:speaker"

    @property
    def should_poll(self) -> bool:
        """Return True if entity has to be polled for state.
        False if entity pushes its state to HA.
        """
        return False

    # pylint: disable=no-self-use
    # Implement these for your media player
    @property
    def state(self):
        # State of the player.
        if self._parent.is_connected:
            if self._parent._mode == MODE_MRAD:
                power = self._parent.get_event(self._zoneId, 'PowerOn')
            else:
                power = 'True' # since MODE_STANDALONE zones (aka instances) are ALWAYS ON

            if power is None:
                return STATE_UNKNOWN
            elif power.find('T')==0:
                mediaControl = self._parent.get_event(self._sourceId,'MediaControl')

                if mediaControl is None:
                    return STATE_ON
                elif mediaControl == 'Pause':
                    return STATE_PAUSED
                elif mediaControl == 'Stop':
                    return STATE_PAUSED
                elif mediaControl == 'Play':
                    return STATE_PLAYING

                return STATE_ON

            return STATE_OFF

        return STATE_OFFLINE

    @property
    def volume_level(self):
        # Volume level of the media player (0..1).
        if self._parent.is_connected:
            if self._parent._mode == MODE_MRAD:
                maxVolume = self._parent.get_event(self._zoneId, 'MaxVolume')
                if self._isGroupZone:
                    volume = self._parent.get_event(self._groupName, 'Volume')
                else:
                    volume = self._parent.get_event(self._zoneId, 'Volume')
            else:
                maxVolume = 50
                gainMode = self._parent.get_event(self._zoneId, 'GainMode')
                if gainMode is None:
                    volume = 50
                elif gainMode == 'Fixed':
                    volume = 50
                else:
                    volume = self._parent.get_event(self._zoneId, 'Volume')


            if maxVolume is None:
                maxVolume = 80
            elif float(maxVolume) == 0:
                maxVolume = 80

            if volume is None:
                volume = 0

            return float(volume) / float(maxVolume)

        return None

    async def async_volume_up(self) -> None:
        """Volume up the media player."""
        if self._parent._mode == MODE_MRAD:
            if self._isGroupZone:
                self._parent.send('mrad.volumeup "{}"'.format(self._groupGuid))
            else:
                self._parent.send('mrad.setzone "{}"'.format(self._zoneId))
                self._parent.send('mrad.volumeup')
        else:
            gainMode = self._parent.get_event(self._zoneId, 'GainMode')
            if gainMode is not None and gainMode == 'Fixed':
                return

            self._parent.send('setInstance "{}"'.format(self._sourceId))
            self._parent.send('volumeup')

    async def async_volume_down(self) -> None:
        """Volume down the media player."""
        if self._parent._mode == MODE_MRAD:
            if self._isGroupZone:
                self._parent.send('mrad.volumedown "{}"'.format(self._groupGuid))
            else:
                self._parent.send('mrad.setzone "{}"'.format(self._zoneId))
                self._parent.send('mrad.volumedown')
        else:
            gainMode = self._parent.get_event(self._zoneId, 'GainMode')
            if gainMode is not None and gainMode == 'Fixed':
                return

            self._parent.send('setInstance "{}"'.format(self._sourceId))
            self._parent.send('volumedown')

    @property
    def is_volume_muted(self):
        # Boolean if volume is currently muted.
        if self._parent.is_connected:
            mute = self._parent.get_event(self._zoneId, 'Mute')

            if mute is None:
                return None

            return mute.find('T')==0

        return None

    """
    @property
    def media_content_id(self):
        # Content ID of current playing media.
        return None
    """

    @property
    def media_content_type(self):
        # Content type of current playing media.
        if self._parent.is_connected:
            mediaControl = self._parent.get_event(self._sourceId,'MediaControl')

            if mediaControl is None:
                return None
            elif mediaControl == 'Stop':
                return None

            return MEDIA_TYPE_MUSIC

        return None

    @property
    def media_duration(self):
        # Duration of current playing media in seconds.
        if self._parent.is_connected:
            duration = self._parent.get_event(self._sourceId,'TrackDuration')

            if duration is None:
                return None

            duration = duration.replace("00:00:00", "0")

            if int(duration)==0:
                return None

            return int(duration)

        return None

    @property
    def media_position(self):
        # Position of current playing media in seconds.
        if self._parent.is_connected:
            position = self._parent.get_event(self._sourceId,'TrackTime')

            if position is None:
                return None

            position = position.replace("00:00:00", "0")

            if int(position)==0:
                return None

            return int(position)

        return None


    @property
    def media_position_updated_at(self):
        # When was the position of the current playing media valid.
        # Returns value from homeassistant.util.dt.utcnow().
        if self._parent.is_connected:
            position_utc = self._parent.get_event(self._sourceId,'TrackTimeUtc')

            if position_utc is None:
                return None

            return position_utc

        return None

    @property
    def media_image_url(self):
        # From ZoneGroups
        # Image url of current playing media.
        if self._parent.is_connected:
            mArt = self._parent.get_event(self._sourceId,'mArt')

            if mArt is None:
                return None

            return mArt

        return None

    @property
    def media_title(self):
        # Title of current playing media.
        if self._parent.is_connected:
            md4 = self._parent.get_event(self._sourceId,'MetaData4')

            if md4 is None:
                return None

            return md4

        return None

    @property
    def media_artist(self):
        # Artist of current playing media, music track only.
        if self._parent.is_connected:
            md2 = self._parent.get_event(self._sourceId,'MetaData2')

            if md2 is None:
                return None

            return md2

        return None

    @property
    def media_album_name(self):
        # Album name of current playing media, music track only.
        if self._parent.is_connected:
            md3 = self._parent.get_event(self._sourceId,'MetaData3')

            if md3 is None:
                return None

            return md3

        return None

    """
    @property
    def media_album_artist(self):
        # Album artist of current playing media, music track only.
        return None

    @property
    def media_track(self):
        # Track number of current playing media, music track only.
        return None

    @property
    def media_series_title(self):
        # Title of series of current playing media, TV show only.
        return None

    @property
    def media_season(self):
        # Season of current playing media, TV show only.
        return None

    @property
    def media_episode(self):
        # Episode of current playing media, TV show only.
        return None

    """

    @property
    def media_channel(self):
        # Channel currently playing.
        if self._parent.is_connected:
            md1 = self._parent.get_event(self._sourceId,'MetaData1')

            if md1 is None:
                return None

            return md1

        return None

    """
    @property
    def media_playlist(self):
        # Title of Playlist currently playing.
        return None

    @property
    def app_id(self):
        # ID of the current running app.
        return None

    @property
    def app_name(self):
        # Name of the current running app.
        return None

    """

    @property
    def source(self):
        # Name of the current input source.
        sourceName = None
        if self._parent.is_connected:
            if sourceName is None:
                sourceName = self._parent.get_event(self._sourceId, 'QualifiedSourceName')

            if sourceName == "":
                sourceName = None
            else:
                sourceName = sourceName.split("@")[0]

            if sourceName is None:
                sourceName = self._parent.get_event(self._sourceId, 'SourceName')

            if sourceName == "":
                sourceName = None

        return sourceName

    @property
    def source_list(self):
        # From ZoneGroups
        # List of available input sources.
        if self._parent.is_connected:
            sourceList = self._parent.get_event(self._zoneId, 'SourceList')

            if sourceList is None:
                return None

            return sourceList

        return None

    @property
    def shuffle(self):
        # Boolean if shuffle is enabled.
        if self._parent.is_connected:
            shuffle = self._parent.get_event(self._sourceId,'Shuffle')

            if shuffle is None:
                return None

            return shuffle.find('T')==0

        return None

    @property
    def supported_features(self):
        # Flag media player features that are supported.
        s = 0

        if self._parent.is_connected:

            smartSource = self._parent.get_event(self._sourceId,'SmartSource')

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

                if self._parent._mode == MODE_STANDALONE:
                    #ReportState Player_A SkipNextAvailable=True
                    b = self._parent.get_event(self._sourceId, 'SkipNextAvailable')
                    if b is not None and b.find('T')==0:
                        s = s | MediaPlayerEntityFeature.NEXT_TRACK

                    #ReportState Player_A SkipPrevAvailable=True
                    b = self._parent.get_event(self._sourceId, 'SkipPrevAvailable')
                    if b is not None and b.find('T')==0:
                        s = s | MediaPlayerEntityFeature.PREVIOUS_TRACK

                    #ReportState Player_A ShuffleAvailable=True
                    b = self._parent.get_event(self._sourceId, 'ShuffleAvailable')
                    if b is not None and b.find('T')==0:
                        s = s |  MediaPlayerEntityFeature.SHUFFLE_SET

                    #ReportState Player_A SeekAvailable=True
                    b = self._parent.get_event(self._sourceId, 'SeekAvailable')
                    if b is not None and b.find('T')==0:
                        s = s |  MediaPlayerEntityFeature.SEEK

                    #ReportState Player_A RepeatAvailable=True
                    #ReportState Player_A PlayPauseAvailable=True
                else:
                    s = s | MediaPlayerEntityFeature.NEXT_TRACK     | \
                            MediaPlayerEntityFeature.PREVIOUS_TRACK | \
                            MediaPlayerEntityFeature.SHUFFLE_SET    | \
                            MediaPlayerEntityFeature.SEEK
            else:

                s = MediaPlayerEntityFeature.VOLUME_STEP     | \
                    MediaPlayerEntityFeature.VOLUME_SET      | \
                    MediaPlayerEntityFeature.VOLUME_MUTE     | \
                    MediaPlayerEntityFeature.TURN_ON         | \
                    MediaPlayerEntityFeature.TURN_OFF        | \
                    MediaPlayerEntityFeature.PLAY_MEDIA      | \
                    MediaPlayerEntityFeature.SELECT_SOURCE   | \
                    MediaPlayerEntityFeature.CLEAR_PLAYLIST

            if self._parent._mode == MODE_STANDALONE:
                s = s & ~MediaPlayerEntityFeature.TURN_ON & ~MediaPlayerEntityFeature.TURN_OFF & ~MediaPlayerEntityFeature.SELECT_SOURCE

                gainMode = self._parent.get_event(self._zoneId, 'GainMode')
                if gainMode is not None and gainMode == 'Fixed':
                    s = s & ~MediaPlayerEntityFeature.VOLUME_SET & ~MediaPlayerEntityFeature.VOLUME_STEP

        return s

    def turn_on(self):
        # Turn the media player on.
        if self._parent._mode == MODE_MRAD:
            self._parent.send('mrad.power on "{}"'.format(self._zoneId))

    def turn_off(self):
        # Turn the media player off.
        if self._parent._mode == MODE_MRAD:
            self._parent.send('mrad.power off "{}"'.format(self._zoneId))

    def mute_volume(self, mute):
        # Mute the volume.
        if mute:
            newState = "on"
        else:
            newState = "off"

        if self._parent._mode == MODE_MRAD:
            if self._isGroupZone:
                self._parent.send('mrad.mute {} "{}"'.format(newState, self._groupGuid))
            else:
                self._parent.send('mrad.setzone "{}"'.format(self._zoneId))
                self._parent.send('mrad.mute {}'.format(newState))
        else:
            self._parent.send('setInstance "{}"'.format(self._sourceId))
            self._parent.send('mute {}'.format(newState))

    def set_volume_level(self, volume):
        # Set volume level, range 0..1.
        if self._parent._mode == MODE_MRAD:
            maxVolume = self._parent.get_event(self._zoneId, 'MaxVolume')

            if maxVolume is None:
                maxVolume = 80

            volume = int( float(volume) * float(maxVolume) )

            if self._isGroupZone:
                self._parent.send('mrad.volume {} {}'.format(volume, self._groupGuid))
            else:
                self._parent.send('mrad.setzone "{}"'.format(self._zoneId))
                self._parent.send('mrad.volume {}'.format(volume))
        else:
            gainMode = self._parent.get_event(self._zoneId, 'GainMode')
            if gainMode is not None and gainMode == 'Fixed':
                return

            maxVolume = 50

            volume = int( float(volume) * float(maxVolume) )
            self._parent.send('setInstance "{}"'.format(self._sourceId))
            self._parent.send('setvolume {}'.format(volume))

    def media_play(self):
        # Send play command.
        if self._parent._mode == MODE_MRAD:
            self._parent.send('mrad.setzone "{}"'.format(self._zoneId))
            self._parent.send('mrad.play')
        else:
            self._parent.send('setInstance "{}"'.format(self._sourceId))
            self._parent.send('play')

    def media_pause(self):
        # Send pause command.
        if self._parent._mode == MODE_MRAD:
            self._parent.send('mrad.setzone "{}"'.format(self._zoneId))
            self._parent.send('mrad.pause')
        else:
            self._parent.send('setInstance "{}"'.format(self._sourceId))
            self._parent.send('pause')

    def media_stop(self):
        # Send stop command.
        if self._parent._mode == MODE_MRAD:
            self._parent.send('mrad.setzone "{}"'.format(self._zoneId))
            self._parent.send('mrad.stop')
        else:
            self._parent.send('setInstance "{}"'.format(self._sourceId))
            self._parent.send('stop')

    def media_previous_track(self):
        # Send previous track command.
        if self._parent._mode == MODE_MRAD:
            self._parent.send('mrad.setzone "{}"'.format(self._zoneId))
            self._parent.send('mrad.SkipPrevious')
        else:
            self._parent.send('setInstance "{}"'.format(self._sourceId))
            self._parent.send('SkipPrevious')

    def media_next_track(self):
        # Send next track command.
        if self._parent._mode == MODE_MRAD:
            self._parent.send('mrad.setzone "{}"'.format(self._zoneId))
            self._parent.send('mrad.SkipNext')
        else:
            self._parent.send('setInstance "{}"'.format(self._sourceId))
            self._parent.send('SkipNext')

    def media_seek(self, position):
        # Send seek command.
        if self._parent._mode == MODE_MRAD:
            self._parent.send('mrad.setzone "{}"'.format(self._zoneId))
            self._parent.send('mrad.setsource')
        else:
            self._parent.send('setInstance "{}"'.format(self._sourceId))

        self._parent.send('seek {}'.format(int(position)))
        # Invalidate TrackTime so it gets updated next report
        self._parent.pop_event(self._sourceId,'TrackTime')

    def play_media(self, media_type, media_id, **kwargs):
        # Play a piece of media.
        # <ServiceCall media_player.play_media: media_content_type=music, media_content_id=http://192.168.13.91:8123/api/tts_proxy/74a4297365735b6c107b85e034347ce013eeae01_en_-_google.mp3, entity_id=['media_player.mt_office']>
        if self._parent._mode == MODE_MRAD:
            self._parent.send('mrad.setzone "{}"'.format(self._zoneId))
            self._parent.send('mrad.setsource')
        else:
            self._parent.send('setInstance "{}"'.format(self._sourceId))

        media_type = media_type.lower()

        if media_source.is_media_source_id(media_id):
            media_type = "music"
            media_id = (
                asyncio.run_coroutine_threadsafe(
                    media_source.async_resolve_media(
                        self._hass, media_id, self.entity_id
                    ),
                    self._hass.loop,
                )
                .result()
                .url
            )
            media_id = async_process_play_media_url(self.hass, media_id)

        if media_type == "music":
            self._parent.send('duckplay "{}"'.format(media_id))
        elif media_type == "scene":
            self._parent.send('recallscene "{}"'.format(media_id))
        elif media_type == "preset":
            self._parent.send('recallpreset "{}"'.format(media_id))
        elif media_type == "radiostation":
            self._parent.send('playradiostation "{}"'.format(media_id))
        elif media_type == "command":
            self._parent.send('{}'.format(media_id))
        else:
            _LOGGER.error("play_media:Unexpected media_type='%s'.", media_type)

    def select_source(self, source):
        # Select input source.
        if self._parent._mode == MODE_MRAD:
            self._parent.send('mrad.setzone "{}"'.format(self._zoneId))
            self._parent.send('mrad.setsource "{}"'.format(source))

    def clear_playlist(self):
        # Clear players playlist.
        if self._parent._mode == MODE_MRAD:
            self._parent.send('mrad.setzone "{}"'.format(self._zoneId))
            self._parent.send('mrad.setsource')
        else:
            self._parent.send('setInstance "{}"'.format(self._sourceId))

        self._parent.send('clearnowplaying false')


    def set_shuffle(self, shuffle):
        # Clear players playlist.
        if self._parent._mode == MODE_MRAD:
            self._parent.send('mrad.setzone "{}"'.format(self._zoneId))
            self._parent.send('mrad.setsource')
        else:
            self._parent.send('setInstance "{}"'.format(self._sourceId))

        if shuffle:
            self._parent.send('shuffle true')
        else:
            self._parent.send('shuffle false')

    @property
    def state_attributes(self):
        """Return the state attributes."""
        state_attr = {}

        if self.state != STATE_OFF:
            state_attr = {
                attr: getattr(self, attr) for attr
                in ATTR_TO_PROPERTY if getattr(self, attr) is not None
            }

        if state_attr is not None:
            state_attr[ATTR_SYSTEM_ID] = self._parent.id
            state_attr[ATTR_PLATFORM ] = DATA_AUTONOMIC
            state_attr[ATTR_VERSION  ] = self._parent.version
            state_attr[ATTR_MODE     ] = self._parent._mode

        return state_attr
