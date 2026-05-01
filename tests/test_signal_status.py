import pytest
from acmax24 import ACMax24, Input, Output


# --- Input class unit tests ---

def test_initial_signal_status_is_zero():
    inp = Input(3)
    assert inp.signal_status == 0
    assert inp.has_audio is False


def test_signal_status_updated_via_event():
    inp = Input(3)
    inp._process_event(['SIG', 'STA', '3'])
    assert inp.signal_status == 3
    assert inp.has_audio is True


def test_signal_status_cleared_via_event():
    inp = Input(3)
    inp._process_event(['SIG', 'STA', '3'])
    inp._process_event(['SIG', 'STA', '0'])
    assert inp.signal_status == 0
    assert inp.has_audio is False


@pytest.mark.parametrize("status,has_audio", [
    (0, False),
    (1, True),   # left only
    (2, True),   # right only
    (3, True),   # both
])
def test_has_audio_reflects_any_channel(status, has_audio):
    inp = Input(1)
    inp._process_event(['SIG', 'STA', str(status)])
    assert inp.has_audio is has_audio


def test_non_sig_events_still_handled_by_base():
    inp = Input(3)
    inp._process_event(['EN'])
    assert inp.enabled is True


def test_non_sig_events_disable():
    inp = Input(3)
    inp._process_event(['EN'])
    inp._process_event(['DIS'])
    assert inp.enabled is False


# --- ACMax24._process_event integration tests ---

def _make_matrix(notify):
    """Construct an ACMax24 without starting the transport, for testing _process_event."""
    matrix = ACMax24.__new__(ACMax24)
    matrix._inputs = [Input(i) for i in range(25)]
    matrix._outputs = [Output(i) for i in range(25)]
    matrix._initial_io_config_received = True
    matrix._errors = 0
    matrix._notify_callback = notify
    return matrix


@pytest.mark.asyncio
async def test_in_prefixed_sig_sta_event_updates_input():
    fired = []
    async def notify():
        fired.append(True)

    matrix = _make_matrix(notify)
    await matrix._process_event("IN3 SIG STA 3\r\n")

    assert matrix._inputs[3].signal_status == 3
    assert matrix._inputs[3].has_audio is True
    assert len(fired) == 1


@pytest.mark.asyncio
async def test_in_prefixed_sig_sta_different_inputs():
    fired = []
    async def notify():
        fired.append(True)

    matrix = _make_matrix(notify)
    await matrix._process_event("IN1 SIG STA 1\r\n")
    await matrix._process_event("IN24 SIG STA 2\r\n")

    assert matrix._inputs[1].signal_status == 1
    assert matrix._inputs[24].signal_status == 2
    assert len(fired) == 2


@pytest.mark.asyncio
async def test_notify_not_fired_before_initial_config():
    fired = []
    async def notify():
        fired.append(True)

    matrix = _make_matrix(notify)
    matrix._initial_io_config_received = False

    await matrix._process_event("IN3 SIG STA 3\r\n")

    # State is updated even without initial config, but callback is suppressed
    assert matrix._inputs[3].signal_status == 3
    assert len(fired) == 0


@pytest.mark.asyncio
async def test_signal_status_going_silent_fires_callback():
    fired = []
    async def notify():
        fired.append(True)

    matrix = _make_matrix(notify)
    await matrix._process_event("IN3 SIG STA 3\r\n")
    await matrix._process_event("IN3 SIG STA 0\r\n")

    assert matrix._inputs[3].signal_status == 0
    assert matrix._inputs[3].has_audio is False
    assert len(fired) == 2
