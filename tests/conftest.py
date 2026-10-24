import pytest
from acmax24 import ACMax24


class FakeTransport:
    """Stands in for Transport; records every message sent instead of using a websocket."""

    def __init__(self):
        self.sent = []

    async def send(self, message: str):
        self.sent.append(message)

    def stop(self):
        pass


@pytest.fixture
def notifications():
    return []


@pytest.fixture
def matrix(notifications):
    async def notify():
        notifications.append(True)

    m = ACMax24("matrix.invalid", notify)
    m._transport = FakeTransport()
    return m


@pytest.fixture
def configured_matrix(matrix):
    """A matrix that has received its initial config, so notify callbacks fire."""
    matrix._initial_io_config_received = True
    return matrix
