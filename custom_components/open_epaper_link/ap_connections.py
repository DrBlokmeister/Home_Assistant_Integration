"""Manage independent WebSocket sessions for OpenEPaperLink APs."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from aiohttp import (
    ClientConnectionError,
    ClientError,
    ClientSession,
    ClientWSTimeout,
    WSMsgType,
)

_LOGGER = logging.getLogger(__name__)
_CONNECT_TIMEOUT = 10
_HEARTBEAT_INTERVAL = 30
_WS_TIMEOUT = ClientWSTimeout(ws_close=5)


class APConnections:
    """Own one connection and retry loop per AP until removed or stopped."""

    def __init__(
        self,
        session: ClientSession,
        *,
        on_message: Callable[[str, str], Awaitable[bool | None]],
        on_connection: Callable[[str, bool], None],
        on_ready: Callable[[str], Awaitable[None]] | None = None,
        verify: Callable[[str], Awaitable[bool]] | None = None,
        create_task: Callable[[Coroutine[Any, Any, None], str], asyncio.Task[None]]
        | None = None,
        reconnect_interval: float = 30,
    ) -> None:
        self._session = session
        self._on_message = on_message
        self._on_connection = on_connection
        self._on_ready = on_ready
        self._verify = verify
        self._create_task = create_task
        self._reconnect_interval = reconnect_interval
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._connected: set[str] = set()
        self._stopped = False

    @property
    def hosts(self) -> frozenset[str]:
        """Return the APs being managed, including offline APs."""
        return frozenset(self._tasks)

    def online(self, host: str) -> bool:
        """Return whether this AP currently has an open WebSocket."""
        return host in self._connected

    def start(self, host: str) -> None:
        """Start the AP's connection loop if it is new to this manager."""
        if self._stopped or host in self._tasks:
            return

        connection = self._run(host)
        name = f"open_epaper_link_websocket_{host}"
        if self._create_task is None:
            self._tasks[host] = asyncio.create_task(connection, name=name)
        else:
            self._tasks[host] = self._create_task(connection, name)

    async def remove(self, host: str) -> None:
        """Cancel and join one AP's session and retry loop."""
        if (task := self._tasks.get(host)) is None:
            return

        if not task.cancelling():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if self._tasks.get(host) is task:
            del self._tasks[host]

    async def stop(self) -> None:
        """Stop all sessions and wait until their callbacks have finished."""
        self._stopped = True
        tasks = tuple(self._tasks.values())
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    def _set_online(self, host: str, online: bool) -> None:
        if online == self.online(host):
            return
        if online:
            self._connected.add(host)
        else:
            self._connected.discard(host)
        self._on_connection(host, online)

    async def _run(self, host: str) -> None:
        while not self._stopped:
            try:
                async with asyncio.timeout(_CONNECT_TIMEOUT):
                    if self._verify is not None and not await self._verify(host):
                        raise ClientConnectionError("AP identity verification failed")
                    websocket = await self._session.ws_connect(
                        f"ws://{host}/ws", heartbeat=_HEARTBEAT_INTERVAL, timeout=_WS_TIMEOUT
                    )
                async with websocket:
                    try:
                        self._set_online(host, True)
                        async with asyncio.TaskGroup() as tasks:
                            hydration = (
                                tasks.create_task(self._on_ready(host))
                                if self._on_ready is not None
                                else None
                            )
                            try:
                                async for message in websocket:
                                    if message.type == WSMsgType.ERROR:
                                        break
                                    if (
                                        message.type == WSMsgType.TEXT
                                        and await self._on_message(host, message.data)
                                    ):
                                        break
                            finally:
                                if hydration is not None:
                                    hydration.cancel()
                    finally:
                        self._set_online(host, False)
            except (ClientError, TimeoutError) as err:
                _LOGGER.debug("WebSocket connection to %s failed: %s", host, err)
            except Exception:
                _LOGGER.exception("Unexpected WebSocket error for %s", host)

            await asyncio.sleep(self._reconnect_interval)
