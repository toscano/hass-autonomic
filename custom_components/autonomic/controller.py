"""Support for AVPro AV-MX-nn matrix switches."""
from __future__ import annotations
from typing import List, Callable

import logging
from typing import Any

import voluptuous as vol

import aiohttp
import asyncio
import async_timeout
import json
import xmltodict

from distutils.version import LooseVersion
from homeassistant.config_entries import ConfigFlow
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.util.dt as dt_util

from .const import DOMAIN, MIN_VERSION_REQUIRED, MODE_UNKNOWN,  MODE_STANDALONE, MODE_MRAD, RETRY_CONNECT_SECONDS, PING_INTERVAL, TICK_THRESHOLD_SECONDS, TICK_UPDATE_SECONDS

LOGGER = logging.getLogger(__package__)

class Controller:
    """Controller for talking to the AVPro Matrix switch."""

    def __init__(self, hass: HomeAssistant, session: aiohttp.ClientSession, host: str, name: str = "", uuid: str = "", mode: str = MODE_UNKNOWN, zones: list = [], instances: list = []) -> None:
        """Init."""
        self._hass = hass
        self._session = session
        self._host: str = host
        self._port: int = 5004
        self._name: str = name
        self._uuid: str = uuid
        self._mode: str = mode
        self._zones: list = zones
        self._instances: list = instances

        self._version: str = ""

        self._zoneEntities = []
        self._zoneEntitiesByGuid = {}
        self.is_connected = False
        self._events = {}


    async def async_check_connection(self) -> bool:
        LOGGER.debug(f"Testing connection to {self._host}.")

        url = f"http://{self._host}:5005/upnp/DevDesc/0.xml"

        with async_timeout.timeout(10):
            response = await self._session.get(url)

        body = await response.text()
        #LOGGER.debug(body)

        data = xmltodict.parse(body)

        # Use the License GUID as the unique id for this streamer
        # or, if you can't find it, use the upnp UDN.
        idx = body.find("<!-- LID:")
        if idx >= 0:
            self._uuid = body[idx+9:idx+9+36]
        else:
            self._uuid = data['root']['device']['UDN'][5:5+36]

        LOGGER.debug(f"License ID: {self._uuid}")

        self._name = data['root']['device']['friendlyName']
        LOGGER.debug(f"Name: {self._name}")

        # Min version check if not running Debug bits
        self._version = data['root']['device']['modelNumber']
        LOGGER.debug(f"Version: {self._version}")

        version = self._version
        idx = version.find('Debug')
        if idx < 0:
            idx = version.find(' ')
            if idx >= 0:
                version = version[:idx]

            if  LooseVersion(version) < LooseVersion(MIN_VERSION_REQUIRED):
                LOGGER.error(f"Server at {self._host} is running {self._version}. Min required is {MIN_VERSION_REQUIRED}.")
                raise ValueError

        # Are we running in MRAD or STAND_ALONE mode?
        url = f"http://{self._host}/MirageCfg/jsonModel?t=SystemSettingsModel&_=1"

        with async_timeout.timeout(10):
            response = await self._session.get(url)

        json = await response.json()
        self._mode = MODE_STANDALONE
        if json and json["Configured"]:
            for item in json["Configured"]:
                if item["DeviceType"] == "MMS":
                    LOGGER.debug(f"MMS found in stack {item['Id']}")
                    url = f"http://{self._host}/MirageCfg/jsonModel?t=ServerDetailsModel&id={item['Id']}&_=1"
                    with async_timeout.timeout(10):
                        response = await self._session.get(url)
                    mmsJson = await response.json()
                    for output in mmsJson["Outputs"]:
                        if output["IsEnabled"]:
                            self._instances.append(output["Name"])
                elif item["DeviceType"] == "AMP":
                    self._mode = MODE_MRAD
                    LOGGER.debug(f"Found {item['DeviceType']} - {item['DeviceModel']} - {item['Zones']}")
                    splits = item['Zones'].split('-')
                    f = int(splits[0])
                    t = int(splits[1])+1
                    for i in range(f,t):
                        self._zones.append(i)

        self._zones.sort()
        LOGGER.debug("async_check_connections succeeded.")

        return True


    async def async_connect_to_mms(self) -> None:
        """
        Connect to the server and start processing responses.
        """
        self._closing = False
        self.is_connected = False
        self._last_inbound_data_utc = dt_util.utcnow()
        self._sent_ping = 0

        # If we have any Zones... get them to update their state to OFFLINE
        for zone in self._zoneEntities:
            zone.update_ha()

        # Now open the socket
        workToDo = True
        while workToDo:
            try:
                if self._closing:
                    return

                LOGGER.info(f"Connecting to {self._host}:{self._port}")

                reader, writer = await asyncio.open_connection(self._host, self._port)
                workToDo = False
            except:
                LOGGER.warn(f"Connection to {self._host}:{self._port} failed... will try again in {RETRY_CONNECT_SECONDS} seconds.")
                await asyncio.sleep(RETRY_CONNECT_SECONDS)

        # reset the pending commands
        self._cmd_queue = asyncio.Queue()

        self._events = {}

        # Get the Zone structure
        self.send('setclienttype hass')
        self.send('setxmlmode lists')

        # The order is important here!
        # Get the events FIRST so values wont be None.
        if self._mode == MODE_STANDALONE:
            # Subscribe and catchup
            self.send('subscribeevents')
            self.send('getstatus')

            self.send('browseinstances')
        else:
            # Subscribe and catchup
            self.send('mrad.subscribeevents')
            self.send('mrad.getstatus')
            self.send('subscribeevents')
            self.send('getstatus')

            self.send('mrad.browseallzones')
            self.send('mrad.browsezonegroups')


        self.async_io_loop_future = asyncio.ensure_future(self.async_io_loop(reader, writer))

        LOGGER.info(f"Connected to {self._host}:{self._port}")
        self.is_connected = True

        return

    async def async_io_loop(self, reader, writer):

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
                    LOGGER.info(f"IO loop with {self._host}:{self._port} exited for local close")
                    return

                if self._net_future in done:

                    if reader.at_eof():
                        self._queue_future.cancel()
                        self._net_future.cancel()
                        LOGGER.info(f"IO loop with {self._host}:{self._port} exited for remote close...")
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
                    #LOGGER.info("%s:--> %s", self.host, cmd)
                    cmd += '\r'
                    writer.write(bytearray(cmd, 'utf-8'))
                    await writer.drain()

                    self._queue_future = asyncio.ensure_future(self._cmd_queue.get())

            LOGGER.debug(f"IO loop with {self._host}:{self._port} exited")

        except GeneratorExit:
            return

        except asyncio.CancelledError:
            LOGGER.debug(f"IO loop with {self._host}:{self._port} cancelled")
            writer.close()
            self._queue_future.cancel()
            self._net_future.cancel()
            raise
        except:
            LOGGER.exception(f"Unhandled exception in IO loop with {self._host}:{self._port}")
            raise


    async def async_disconnect_from_mms(self) -> None:
        LOGGER.info(f"Closing connection to {self._host}:{self._port}")
        self._closing = True
        self._queue_future.cancel()

    async def async_check_ping(self, now=None):
        """Maybe send a ping."""
        if (self.is_connected == False or self._closing):
            return

        if (self._last_inbound_data_utc + PING_INTERVAL + PING_INTERVAL < dt_util.utcnow() ):

            if (self._sent_ping > 2):
                # Schedule a re-connect...
                LOGGER.error(f"PING...{self._host} reconnect needed.")
                self._sent_ping = 0
                self.is_connected = False
                self._hass.async_add_job(self.async_connect_to_mms())
                return
            self._sent_ping = self._sent_ping + 1
            if (self._sent_ping > 1):
                LOGGER.debug(f"PING...{self._host} sending ping {self._sent_ping}")
            self.send("ping")
        elif (self._sent_ping > 0):
            if (self._sent_ping > 1):
                LOGGER.debug(f"PING...{self._host} resetting ping {self._last_inbound_data_utc}")
            self._sent_ping = 0



    def add_zone_entity(self, zone) -> None:
        self._zoneEntities.append(zone)
        return

    def send(self, cmd):
        LOGGER.debug(f"-->{cmd}")
        self._cmd_queue.put_nowait(cmd)

    def get_event(self, entityId, eventName):

        key = f'{entityId}.{eventName}'
        if key not in self._events:
            return None
        else:
            return self._events[key]

    def _process_response(self, res):
        try:

            s = str(res, 'utf-8').strip()
            # _LOGGER.debug("%s:<--%s", self.host, s)

            if s.startswith('<Zones'):
                self._process_mrad_zone_response(s)
            elif s.startswith('<ZoneGroups'):
                self._process_mrad_zone_group_response(s)
            elif s.startswith('MRAD.'):
                self._process_mrad_event(s)
            #elif s.startswith('<Instances'):
            #    self._process_standalone_instance_response(s)
            elif s.startswith('ReportState') or s.startswith('StateChanged'):
                self._process_instance_event(s)
            #else:
            #    _LOGGER.info("%s:unprocessed<--%s", self.host, s)

            return s

        except Exception as e:
            LOGGER.exception(f"_process_response ex {e}")
            # some error occurred, re-connect may fix that
            self.send('quit')

    def _process_mrad_zone_response(self, res):
        """Response to BrowseAllZones"""
        data = xmltodict.parse(res, force_list=('Zone',))

        #  There's a chance that the Zone count is zero while the MMS is starting up... That's handled as an exception/reconnect
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
            sourceId= f"Source_{zone['@sourceId']}"
            name    = zone['@name']
            id      = zone['@id']

            if guid in self._zoneEntitiesByGuid:
                found = self._zoneEntitiesByGuid[guid]
                found.set_name_source_and_group( newName = name, newSourceId = sourceId )
            else:
                # mrad zone
                found = None
                for zone in self._zoneEntities:
                    if zone._mms_zone_id == id:
                        LOGGER.info(f"DISCOVERED MRAD ZONE: {id} {name}")
                        found = zone
                        break
                if found is not None:
                    self._zoneEntitiesByGuid[guid] = found
                    found.set_name_source_and_group( newName = name, newSourceId = sourceId )

    def _process_mrad_zone_group_response(self, res):
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
            sourceId = f"Source_{sId}"
            mArt     = group.get('@mArt', "" )

            if mArt == "":
                self._events[f'{sourceId}.mArt'         ]=None
                self._events[f'{sourceId}.MetaData1'    ]=None
                self._events[f'{sourceId}.MetaData2'    ]=None
                self._events[f'{sourceId}.MetaData3'    ]=None
                self._events[f'{sourceId}.MetaData4'    ]=None
                self._events[f'{sourceId}.TrackDuration']=None
                self._events[f'{sourceId}.TrackTime'    ]=None
                self._events[f'{sourceId}.TrackTimeUtc' ]=None
                self._events[f'{sourceId}.Shuffle'      ]=None
                self._events[f'{sourceId}.SmartSource'  ]=False
                self._events[f'{sourceId}.MediaControl' ]='Unknown'
            else:
                self._events[f'{sourceId}.mArt'         ]=mArt
                self._events[f'{sourceId}.SmartSource'  ]=True

            sources = []

            # Make sure we've got a list to process which isn't true if there
            # is only one source enabled on the MMS. Crazy right?
            if isinstance(group['Sources']['Source'], list):
                sourcesList = group['Sources']['Source']
            else:
                sourcesList = [group['Sources']['Source']]

            for source in sourcesList:
                fqn = source.get('@name', "")
                if fqn == "":
                    fqn = source['@fqn'].split("@")[0].replace('_', ' ')

                # Add that to the list of ALL sources for this (these) zone(s)
                sources.append(fqn)

                # And make sure that's correct in the event table
                sid = source.get('@sId', "")
                key = f'Source_{sid}.QualifiedSourceName'
                self._events[key] = fqn.replace(' ', '_')

            # Now set the available sources into the zone (zones)
            for vZone in group['vol']['zone']:
                name = vZone['@name']
                eventId = vZone['@eventId']
                key = f'{eventId}.SourceList'
                self._events[key]=sources

                # Ensure that the sourceId is set correctly for the zone
                guid = vZone["@guid"]
                if guid in self._zoneEntitiesByGuid:
                    found = self._zoneEntitiesByGuid[guid]
                    found.set_name_source_and_group( newName = name, newSourceId = sourceId, newGroupGuid = groupGuid, newGroupName = groupName )
                else:
                    found = None
                    for zone in self._zoneEntities:
                        if zone._mms_zone_id == eventId:
                            LOGGER.info(f"DISCOVERED MRAD ZONE: {eventId} {name}")
                            found = zone
                            break
                    if found is not None:
                        self._zoneEntitiesByGuid[guid] = found
                        found.set_name_source_and_group( newName = name, newSourceId = sourceId, newGroupGuid = groupGuid, newGroupName = groupName )

    def _process_mrad_event(self, res):
        # Parse...
        # MRAD.ReportState Zone_1 ZoneGain=0
        splits = res.split(' ')
        nv = splits[2].split('=')
        eventName = nv[0]
        pEq = res.find('=')
        entityId = splits[1]

        key = f'{entityId}.{eventName}'
        eventValue = res[pEq+1:]

        # Update our object for the first few TrackTime events
        # then only once every TICK_UPDATE_SECONDS
        if eventName == 'TrackTime':
            eventValue = eventValue.replace("00:00:00", "0")
            if key in self._events:
                if int(eventValue) > TICK_THRESHOLD_SECONDS:
                    if int(eventValue) % TICK_UPDATE_SECONDS != 0:
                        return

        self._events[key]=eventValue

        # Manufacture TrackTimeUtc and since TrackTime
        # only occurs for SmartSources manufacture that too...
        if eventName == 'TrackTime':
            eventName = 'TrackTimeUtc'
            key = f'{entityId}.{eventName}'
            eventValue = dt_util.utcnow()
            self._events[key]=eventValue

            eventName = 'SmartSource'
            key = f'{entityId}.{eventName}'
            eventValue = True
            self._events[key]=eventValue

        # Schedule an update for the associated Zone(s)
        for zone in self._zoneEntities:
            if zone._mms_zone_id == entityId:
                zone.update_ha()
            elif zone._mms_source_id == entityId:
                zone.update_ha()

    def _process_instance_event(self, res):
        #LOGGER.debug(f"<--{res}")
        # Parse...
        # StateChanged Player_A TrackTime=263
        splits = res.split(' ')
        nv = splits[2].split('=')
        eventName = nv[0]
        pEq = res.find('=')
        entityId = splits[1]

        key = f'{entityId}.{eventName}'
        eventValue = res[pEq+1:]

        # Update our object for the first few TrackTime events
        # then only once every TICK_UPDATE_SECONDS
        if eventName == 'TrackTime':
            eventValue = eventValue.replace("00:00:00", "0")
            if key in self._events:
                if int(eventValue) > TICK_THRESHOLD_SECONDS:
                    if int(eventValue) % TICK_UPDATE_SECONDS != 0:
                        return

        self._events[key]=eventValue

        # Manufacture TrackTimeUtc and since TrackTime
        # only occurs for SmartSources manufacture that too...
        if eventName == 'TrackTime':
            eventName = 'TrackTimeUtc'
            key = f'{entityId}.{eventName}'
            eventValue = dt_util.utcnow()
            self._events[key]=eventValue

            eventName = 'SmartSource'
            key = f'{entityId}.{eventName}'
            eventValue = True
            self._events[key]=eventValue

        if self._mode == MODE_STANDALONE:
            # Shortcut to better art
            if eventName == 'MediaArtChanged':
                self.send('browseinstances')
                return

            # Schedule an update for the associated Zone(s)
            for zone in self._zoneEntities:
                if zone._mms_zone_id == entityId:
                    zone.update_ha()
                elif zone._mms_source_id == entityId:
                    zone.update_ha()

        else:
            for zone in self._zoneEntities:
                key = f'{zone._mms_source_id}.QualifiedSourceName'
                if key in self._events:
                    val = self._events[key]
                    if val is not None:
                        if val == entityId:
                            zone.update_ha()

