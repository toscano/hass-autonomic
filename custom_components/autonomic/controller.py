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
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, MIN_VERSION_REQUIRED, MODE_UNKNOWN,  MODE_STANDALONE, MODE_MRAD

LOGGER = logging.getLogger(__package__)

class Controller:
    """Controller for talking to the AVPro Matrix switch."""

    def __init__(self, session: aiohttp.ClientSession, host: str, name: str = "", uuid: str = "", mode: str = MODE_UNKNOWN, zones: list = [], instances: list = []) -> None:
        """Init."""
        self._session = session
        self._host: str = host
        self._name: str = name
        self._uuid: str = uuid
        self._mode: str = mode
        self._zones: list = zones
        self._instances: list = instances

        self._version: str = ""


    async def async_check_connection(self) -> bool:
        LOGGER.debug(f"Testing connection to {self._host}.")

        url = f"http://{self._host}:5005/upnp/DevDesc/0.xml"

        with async_timeout.timeout(10):
            response = await self._session.get(url)

        body = await response.text()
        #_LOGGER.debug(body)

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


    @property
    def name(self) -> str:
        return self._name

    @property
    def version(self) -> str:
        return self._version
