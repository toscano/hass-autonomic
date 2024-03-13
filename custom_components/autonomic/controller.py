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

from .const import DOMAIN, MIN_VERSION_REQUIRED, MODE_UNKNOWN,  MODE_STANDALONE, MODE_MRAD, RETRY_CONNECT_SECONDS, PING_INTERVAL

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
        self.is_connected = False


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

        version = self.version
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
            self.send('subscribeeventsall')
            self.send('getstatus')

            self.send('mrad.browsezones')
            self.send('mrad.browsezonegroups')


        self._ioloop_future = asyncio.ensure_future(self._ioloop(reader, writer))

        LOGGER.info(f"Connected to {self._host}:{self._port}")
        self.is_connected = True

        return

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
        if (self.is_connected == False):
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

    def _process_response(self, res):
        try:

            s = str(res, 'utf-8').strip()
            LOGGER.debug(f"<--{s}")

        except Exception as e:
            LOGGER.exception(f"_process_response ex {e}")
            # some error occurred, re-connect may fix that
            self.send('quit')

    @property
    def name(self) -> str:
        return self._name

    @property
    def version(self) -> str:
        return self._version
