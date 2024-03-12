"""Support for Autonomic eSeries Media Systems."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
import aiohttp

from homeassistant import config_entries
from homeassistant.components import zeroconf
from homeassistant.config_entries import ConfigFlow
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_UUID, CONF_MODE
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, MODE_UNKNOWN, MIN_VERSION_REQUIRED
from .controller import Controller

LOGGER = logging.getLogger(__package__)


class AutonomicESeriesFlowHandler(ConfigFlow, domain=DOMAIN):
    """Config flow for AV-MX-nn matrix switches."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH

    def __init__(self) -> None:
        """Initialize flow."""
        self._host: str | None = None
        self._name: str | None = None
        self._uuid: str | None = None
        self._version: str | None = None
        self._mode: str = MODE_UNKNOWN

        self._errors: dict[str, str] = {}

    async def async_validate_input(self) -> FlowResult | None:
        """Validate the input Against the device."""

        LOGGER.debug(f"async_validate_input: {self._host} / {self._name} / {self._uuid} / {self._mode}")

        self._errors.clear()

        if self._host is None:
            self._errors["base"] = "cannot_connect"
            LOGGER.error("Cannot connect: Host is none")
            return None

        if self._name is None or self._uuid is None or self._version is None or self._mode == MODE_UNKNOWN:

            session = async_get_clientsession(self.hass)
            client = Controller(session, self._host)

            try:
                await client.async_check_connection()
                LOGGER.debug("No error while checking.")

                self._name = client._name
                self._uuid = client._uuid
                self._version = client._version
                self._mode = client._mode

            except ValueError:
                self._errors["base"] = "min_firmware_required"
                LOGGER.error(f"Your {client._name} is running firmware {client._version} which is less than {MIN_VERSION_REQUIRED}")
                return None

            except aiohttp.ClientConnectorError:
                self._errors["base"] = "cannot_connect"
                LOGGER.error("Cannot connect: Exception")
                return None

            except Exception as e:
                LOGGER.error(f"Unknown exception {type(e)}")
                self._errors["base"] = "unknown_error"
                return None

        LOGGER.debug("About to set unique ID")
        await self.async_set_unique_id(self._uuid)
        self._abort_if_unique_id_configured()

        LOGGER.debug(f"Returning create entry : {self._host} / {self._name} / {self._uuid} / {self._mode}")
        return self.async_create_entry(
            title=self._name,
            data={CONF_HOST: self._host, CONF_NAME: self._name, CONF_UUID: self._uuid, CONF_MODE: self._mode},
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle a flow initialized by the user."""

        if user_input is not None:
            self._host = user_input[CONF_HOST]
            result = await self.async_validate_input()
            if result is not None:
                return result

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
            errors=self._errors,
        )

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:

        if user_input is not None:
            result = await self.async_validate_input()
            if result is not None:
                return result

        return self.async_show_form(
            step_id="confirm",
            errors=self._errors,
            description_placeholders={
                CONF_NAME: self._name,
                CONF_HOST: self._host,
            },
        )

    async def async_step_zeroconf(
        self, discovery_info: zeroconf.ZeroconfServiceInfo
    ) -> FlowResult:
        """Handle zeroconf discovery."""
        LOGGER.debug(f"zeroconf: {discovery_info}")

        self._host = discovery_info.host
        self._name = discovery_info.properties.get("sku")

        self._uuid = discovery_info.properties.get("lid")
        if self._uuid[0:1]=="{":
            self._uuid=self._uuid[1:len(self._uuid)-1]

        self._version = discovery_info.properties.get("vers")

        await self.async_set_unique_id(self._uuid)
        self._abort_if_unique_id_configured()

        self.context["title_placeholders"] = {
            CONF_NAME: self._name,
            CONF_HOST: self._host
        }

        return await self.async_step_confirm()
