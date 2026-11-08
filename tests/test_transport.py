import asyncio
import threading

import pytest
import websockets

import acmax24
from acmax24 import Transport


class FakeSocket:
    """Minimal stand-in for a websocket connection."""

    def __init__(self, messages=(), end_with=None):
        self._messages = list(messages)
        self._end_with = end_with
        self.sent = []
        self.send_threads = []
        self.closed = False
        self.hang = False

    async def send(self, message):
        self.send_threads.append(threading.current_thread())
        if self.hang:
            await asyncio.Event().wait()
        self.sent.append(message)

    async def close(self):
        self.closed = True

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for m in self._messages:
            yield m
        if self._end_with is not None:
            raise self._end_with


@pytest.fixture
def transport_loop():
    """An event loop running in its own thread, like the real transport thread."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    yield loop, thread
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5)
    loop.close()


# --- send() ---

@pytest.mark.asyncio
async def test_send_from_other_loop_runs_on_transport_thread(transport_loop):
    loop, thread = transport_loop
    t = Transport("matrix.invalid", None)
    t._loop = loop
    t.socket = FakeSocket()

    await t.send("SET OUT1 MUTE\r\n")

    assert t.socket.sent == ["SET OUT1 MUTE\r\n"]
    assert t.socket.send_threads == [thread]


@pytest.mark.asyncio
async def test_send_on_transport_loop_sends_directly():
    t = Transport("matrix.invalid", None)
    t._loop = asyncio.get_running_loop()
    t.socket = FakeSocket()

    await t.send("GET CONFIG\r")

    assert t.socket.sent == ["GET CONFIG\r"]
    assert t.socket.send_threads == [threading.current_thread()]


@pytest.mark.asyncio
async def test_send_without_socket_fails_fast():
    t = Transport("matrix.invalid", None)
    t._loop = asyncio.get_running_loop()
    with pytest.raises(ConnectionError):
        await t.send("GET CONFIG\r")


@pytest.mark.asyncio
async def test_send_times_out_instead_of_hanging(transport_loop, monkeypatch):
    monkeypatch.setattr(acmax24, "SEND_TIMEOUT", 0.1)
    loop, _ = transport_loop
    t = Transport("matrix.invalid", None)
    t._loop = loop
    t.socket = FakeSocket()
    t.socket.hang = True

    with pytest.raises(asyncio.TimeoutError):
        await t.send("SET OUT1 MUTE\r\n")


# --- connection handling ---

@pytest.mark.asyncio
async def test_callback_error_does_not_drop_connection():
    received = []

    async def callback(msg):
        if msg == "bad":
            raise RuntimeError("boom")
        received.append(msg)

    t = Transport("matrix.invalid", callback)
    t._loop = asyncio.get_running_loop()
    ws = FakeSocket(messages=["one", "bad", "two"])

    await t._run_connection(ws)

    # Every message after the failing one is still processed on the same connection
    assert received == ["one", "two"]
    assert t.reconnects == 0


@pytest.mark.asyncio
async def test_connection_closed_clears_socket_and_closes():
    t = Transport("matrix.invalid", None)
    t._loop = asyncio.get_running_loop()
    ws = FakeSocket(end_with=websockets.ConnectionClosed(None, None))

    await t._run_connection(ws)

    assert t.socket is None
    assert ws.closed
    assert "connection closed" in t.last_disconnect_reason


@pytest.mark.asyncio
async def test_unexpected_error_clears_socket_and_closes():
    t = Transport("matrix.invalid", None)
    t._loop = asyncio.get_running_loop()
    ws = FakeSocket(end_with=OSError("reset"))

    await t._run_connection(ws)

    assert t.socket is None
    assert ws.closed
    assert "OSError" in t.last_disconnect_reason


@pytest.mark.asyncio
async def test_reconnects_counted():
    t = Transport("matrix.invalid", None)
    t._loop = asyncio.get_running_loop()

    await t._run_connection(FakeSocket())
    assert t.reconnects == 0
    await t._run_connection(FakeSocket())
    await t._run_connection(FakeSocket())
    assert t.reconnects == 2


@pytest.mark.asyncio
async def test_connection_sends_initial_refresh():
    t = Transport("matrix.invalid", None)
    t._loop = asyncio.get_running_loop()
    ws = FakeSocket()

    await t._run_connection(ws)

    assert ws.sent == ["GET CONFIG\r", "GET CONFIG\r", "GET IN0 SIG STA\r"]
