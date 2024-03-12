"""The Autonomic MMS eSeries integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_UUID, CONF_MODE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN
from . import controller

LOGGER = logging.getLogger(__package__)

# List of platforms to support. There should be a matching .py file for each,
# eg <cover.py> and <sensor.py>
PLATFORMS: list[str] = ["media_player"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up our Autonomic MMS from a config entry."""
    # {'host': '192.168.20.80', 'name': 'MMS-5e', 'uuid': 'a5563017-f622-49c2-bb36-b9e472463cd7', 'mode': 'mode_mrad'}

    # Store an instance of the "connecting" class that does the work of speaking
    # with the actual devices.
    LOGGER.info(f"Setting up Autonomic eSeries ID:{entry.entry_id} DATA:{entry.data}")

    session = async_get_clientsession(hass)
    client = controller.Controller(session, entry.data[CONF_HOST], entry.data[CONF_NAME], entry.data[CONF_UUID], entry.data[CONF_MODE])

    return False

    ## check availability and connect the webSocket
    #await client.async_check_connection(True)

    #hass.data.setdefault(DOMAIN, {})[entry.entry_id] = client

    ## This creates each HA object for each platform your device requires.
    ## It's done by calling the `async_setup_entry` function in each platform module.
    #await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    #return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # This is called when an entry/configured device is to be removed. The class
    # needs to unload itself, and remove callbacks. See the classes for further
    # details
    LOGGER.info(f"Unloading Autonomic eSeries ID:{entry.entry_id} DATA:{entry.data}")
    client = hass.data[DOMAIN][entry.entry_id]
    await client.async_ws_close()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
