"""Support for AVPro AV-MX-nn matrix switches."""
from __future__ import annotations
from typing import List, Callable

import logging
from typing import Any

import asyncio
import homeassistant.util.dt as dt_util

from .const import DOMAIN, MIN_VERSION_REQUIRED, MODE_UNKNOWN,  MODE_STANDALONE, MODE_MRAD, RETRY_CONNECT_SECONDS, PING_INTERVAL, TICK_THRESHOLD_SECONDS, TICK_UPDATE_SECONDS
#from .controller import Controller

LOGGER = logging.getLogger(__package__)

class MmsClient:

    def __init__(self, hass, host: str, port: int, instance: str, callback_object) -> None:
        self._hass = hass
        self._host = host
        self._port = port
        self._inst = instance
        self._callback = callback_object

        self._closing = False
        self.is_connected = False
        self._last_inbound_data_utc = dt_util.utcnow()
        self._sent_ping = 0
        self._cmd_queue = None

    async def async_connect(self) -> None:
        """
        Connect to the server and start processing responses.
        """
        self._closing = False
        self.is_connected = False
        self._last_inbound_data_utc = dt_util.utcnow()
        self._sent_ping = 0

        self._callback.mms_connected(self, False)

        # Now open the socket
        workToDo = True
        while workToDo:
            try:
                if self._closing:
                    return

                LOGGER.info(f"{self._inst}:Connecting to {self._host}:{self._port}")

                reader, writer = await asyncio.open_connection(self._host, self._port)
                workToDo = False
            except:
                LOGGER.warn(f"{self._inst}:Connection to {self._host}:{self._port} failed... will try again in {RETRY_CONNECT_SECONDS} seconds.")
                await asyncio.sleep(RETRY_CONNECT_SECONDS)

        # reset the pending commands
        self._cmd_queue = asyncio.Queue()

        self.async_io_loop_future = asyncio.ensure_future(self.async_io_loop(reader, writer))

        LOGGER.info(f"{self._inst}:Connected to {self._host}:{self._port}")
        self.is_connected = True

        self._callback.mms_connected(self, True)


    async def async_disconnect(self) -> None:
        LOGGER.info(f"{self._inst}:Closing connection to {self._host}:{self._port}")
        self._closing = True
        self._queue_future.cancel()


    async def async_check_ping(self) -> None:

        if (self.is_connected == False or self._closing):
            return

        if (self._last_inbound_data_utc + PING_INTERVAL + PING_INTERVAL < dt_util.utcnow() ):

            if (self._sent_ping > 2):
                # Schedule a re-connect...
                LOGGER.error(f"{self._inst}:PING...{self._host} reconnect needed.")
                self._sent_ping = 0
                self.is_connected = False
                self._hass.async_create_task(self.async_connect(), "MMS re-connect required.")
                return
            self._sent_ping = self._sent_ping + 1
            if (self._sent_ping > 1):
                LOGGER.debug(f"{self._inst}:PING...{self._host} sending ping {self._sent_ping}")
            self.send("ping")
        elif (self._sent_ping > 0):
            if (self._sent_ping > 1):
                LOGGER.debug(f"{self._inst}:PING...{self._host} resetting ping {self._last_inbound_data_utc}")
            self._sent_ping = 0


    def send(self, cmd):
        LOGGER.debug(f"{self._inst}:-->{cmd}")
        self._cmd_queue.put_nowait(cmd)

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
                    LOGGER.info(f"{self._inst}:IO loop with {self._host}:{self._port} exited for local close")
                    return

                if self._net_future in done:

                    if reader.at_eof():
                        self._queue_future.cancel()
                        self._net_future.cancel()
                        LOGGER.info(f"{self._inst}:IO loop with {self._host}:{self._port} exited for remote close...")
                        return

                    response = self._net_future.result()
                    self._last_inbound_data_utc = dt_util.utcnow()
                    try:
                        await self._callback.async_mms_process_response(self, response)
                    except Exception as e:
                        LOGGER.error(f"{self._inst}:async_io_loop:process:{e}")
                        pass

                    self._net_future = asyncio.ensure_future(reader.readline())

                if self._queue_future in done:
                    cmd = self._queue_future.result()
                    #LOGGER.info("%s:--> %s", self.host, cmd)
                    cmd += '\r'
                    writer.write(bytearray(cmd, 'utf-8'))
                    await writer.drain()

                    self._queue_future = asyncio.ensure_future(self._cmd_queue.get())


        except GeneratorExit:
            return

        except asyncio.CancelledError:
            LOGGER.debug(f"{self._inst}:IO loop with {self._host}:{self._port} cancelled")
            writer.close()
            self._queue_future.cancel()
            self._net_future.cancel()
            raise
        except:
            LOGGER.exception(f"{self._inst}:Unhandled exception in IO loop with {self._host}:{self._port}")
            raise

