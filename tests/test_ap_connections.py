"""Exercise AP WebSocket lifecycle with fake sessions."""

from __future__ import annotations

import asyncio
import importlib.util
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import WSMessage, WSMsgType

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components/open_epaper_link/ap_connections.py"
)
spec = importlib.util.spec_from_file_location("ap_connections", MODULE_PATH)
assert spec and spec.loader
ap_connections = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ap_connections)

PRIMARY = "192.0.2.10"
SECONDARY = "192.0.2.11"


class FakeWebSocket:
    """Provide queued frames and observable context cleanup."""

    def __init__(self) -> None:
        self.messages: asyncio.Queue[WSMessage | None] = asyncio.Queue()
        self.closed = asyncio.Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed.set()

    async def __aiter__(self):
        while (message := await self.messages.get()) is not None:
            yield message


class FakeSession:
    """Control each AP's handshake independently."""

    def __init__(self) -> None:
        self.results: dict[str, asyncio.Queue[FakeWebSocket]] = defaultdict(
            asyncio.Queue
        )
        self.attempts: asyncio.Queue[str] = asyncio.Queue()

    async def ws_connect(self, url: str, **_):
        host = urlsplit(url).netloc
        self.attempts.put_nowait(host)
        return await self.results[host].get()


def test_connections_reconnect_independently() -> None:
    async def scenario() -> None:
        session = FakeSession()
        primary, secondary, replacement = (FakeWebSocket() for _ in range(3))
        session.results[PRIMARY].put_nowait(primary)
        session.results[PRIMARY].put_nowait(replacement)
        session.results[SECONDARY].put_nowait(secondary)
        status: asyncio.Queue[tuple[str, bool]] = asyncio.Queue()
        messages: asyncio.Queue[tuple[str, str]] = asyncio.Queue()

        async def on_message(host: str, message: str) -> None:
            messages.put_nowait((host, message))

        connections = ap_connections.APConnections(
            session,
            on_message=on_message,
            on_connection=lambda host, online: status.put_nowait((host, online)),
            reconnect_interval=0,
        )
        async with asyncio.timeout(2):
            try:
                connections.start(PRIMARY)
                connections.start(PRIMARY)
                connections.start(SECONDARY)
                assert {await status.get(), await status.get()} == {
                    (PRIMARY, True),
                    (SECONDARY, True),
                }
                assert connections.hosts == {PRIMARY, SECONDARY}
                primary.messages.put_nowait(None)
                assert await status.get() == (PRIMARY, False)
                assert connections.online(SECONDARY)
                assert await status.get() == (PRIMARY, True)
                assert primary.closed.is_set()
                secondary.messages.put_nowait(WSMessage(WSMsgType.TEXT, "tag", None))
                assert await messages.get() == (SECONDARY, "tag")
                assert [await session.attempts.get() for _ in range(3)].count(
                    SECONDARY
                ) == 1
                assert session.attempts.empty()
            finally:
                await connections.stop()

            assert {await status.get(), await status.get()} == {
                (PRIMARY, False),
                (SECONDARY, False),
            }
            assert secondary.closed.is_set() and replacement.closed.is_set()
            assert not connections.hosts

    asyncio.run(scenario())


def test_handshake_timeout_and_reboot_retry(monkeypatch) -> None:
    monkeypatch.setattr(ap_connections, "_CONNECT_TIMEOUT", 0.05)

    async def scenario() -> None:
        session = FakeSession()
        status: asyncio.Queue[tuple[str, bool]] = asyncio.Queue()

        async def on_message(host: str, message: str) -> bool:
            assert host == PRIMARY
            return message == "REBOOTING"

        connections = ap_connections.APConnections(
            session,
            on_message=on_message,
            on_connection=lambda host, online: status.put_nowait((host, online)),
            reconnect_interval=0,
        )
        async with asyncio.timeout(2):
            try:
                connections.start(PRIMARY)
                assert await session.attempts.get() == PRIMARY
                assert await session.attempts.get() == PRIMARY
                assert not connections.online(PRIMARY)
                assert status.empty()
                socket = FakeWebSocket()
                session.results[PRIMARY].put_nowait(socket)
                assert await status.get() == (PRIMARY, True)
                socket.messages.put_nowait(WSMessage(WSMsgType.TEXT, "REBOOTING", None))
                assert await status.get() == (PRIMARY, False)
                assert await session.attempts.get() == PRIMARY
                assert socket.closed.is_set()
            finally:
                await connections.stop()

            assert not connections.hosts

    asyncio.run(scenario())


def test_verification_runs_before_connect_and_reconnect() -> None:
    async def scenario() -> None:
        session = FakeSession()
        primary, secondary = FakeWebSocket(), FakeWebSocket()
        session.results[PRIMARY].put_nowait(primary)
        session.results[SECONDARY].put_nowait(secondary)
        status: asyncio.Queue[tuple[str, bool]] = asyncio.Queue()
        primary_verdicts: asyncio.Queue[bool] = asyncio.Queue()
        primary_rejections: asyncio.Queue[None] = asyncio.Queue()
        primary_verdicts.put_nowait(False)

        async def verify(host: str) -> bool:
            if host == SECONDARY:
                return True
            verdict = await primary_verdicts.get()
            if not verdict:
                primary_rejections.put_nowait(None)
            return verdict

        async def on_message(_host: str, _message: str) -> None:
            pass

        connections = ap_connections.APConnections(
            session,
            on_message=on_message,
            on_connection=lambda host, online: status.put_nowait((host, online)),
            verify=verify,
            reconnect_interval=0,
        )
        async with asyncio.timeout(2):
            try:
                connections.start(PRIMARY)
                connections.start(SECONDARY)
                await primary_rejections.get()
                assert await status.get() == (SECONDARY, True)
                assert await session.attempts.get() == SECONDARY
                assert session.attempts.empty()
                assert not connections.online(PRIMARY)

                primary_verdicts.put_nowait(True)
                assert await status.get() == (PRIMARY, True)
                assert await session.attempts.get() == PRIMARY

                primary.messages.put_nowait(None)
                primary_verdicts.put_nowait(False)
                assert await status.get() == (PRIMARY, False)
                await primary_rejections.get()
                assert session.attempts.empty()
                assert not connections.online(PRIMARY)
                assert connections.online(SECONDARY)
            finally:
                await connections.stop()

    asyncio.run(scenario())


def test_frames_are_processed_during_hydration_and_cleanup_cancels_it() -> None:
    async def scenario() -> None:
        session = FakeSession()
        primary, secondary = FakeWebSocket(), FakeWebSocket()
        session.results[PRIMARY].put_nowait(primary)
        session.results[SECONDARY].put_nowait(secondary)
        ready: asyncio.Queue[str] = asyncio.Queue()
        completed: asyncio.Queue[str] = asyncio.Queue()
        messages: asyncio.Queue[tuple[str, str]] = asyncio.Queue()

        async def on_ready(host: str) -> None:
            ready.put_nowait(host)
            try:
                await asyncio.Event().wait()
            finally:
                completed.put_nowait(host)

        async def on_message(host: str, message: str) -> None:
            messages.put_nowait((host, message))

        connections = ap_connections.APConnections(
            session,
            on_message=on_message,
            on_connection=lambda *_: None,
            on_ready=on_ready,
        )
        async with asyncio.timeout(2):
            try:
                connections.start(PRIMARY)
                connections.start(SECONDARY)
                assert {await ready.get(), await ready.get()} == {PRIMARY, SECONDARY}
                secondary.messages.put_nowait(WSMessage(WSMsgType.TEXT, "tag", None))
                assert await messages.get() == (SECONDARY, "tag")
                assert completed.empty()
                await connections.remove(PRIMARY)
                assert await completed.get() == PRIMARY
                assert connections.hosts == {SECONDARY}
                assert not connections.online(PRIMARY)
                assert connections.online(SECONDARY)
                assert primary.closed.is_set()
                assert not secondary.closed.is_set()
            finally:
                await connections.stop()

            assert await completed.get() == SECONDARY
            assert secondary.closed.is_set()

    asyncio.run(scenario())
