import pytest
from acmax24 import Input, Output


# --- Output event parsing ---

def test_output_defaults():
    out = Output(5)
    assert out.index == 5
    assert out.label == "unset"
    assert out.enabled is False
    assert out.volume == -1
    assert out.muted is False
    assert out.input_channel == -1


def test_output_volume_event():
    out = Output(1)
    out._process_event(["VOL", "42"])
    assert out.volume == 42


@pytest.mark.parametrize("value,locked", [("LOCK", True), ("UNLOCK", False)])
def test_output_volume_lock_event(value, locked):
    out = Output(1)
    out._process_event(["VOL", "30"])
    out._process_event(["VOL", value])
    assert out._volume_lock is locked
    # A lock event must not clobber the volume
    assert out.volume == 30


def test_output_mute_unmute_events():
    out = Output(1)
    out._process_event(["MUTE"])
    assert out.muted is True
    out._process_event(["UNMUTE"])
    assert out.muted is False


def test_output_input_channel_event():
    out = Output(1)
    out._process_event(["AS", "IN7"])
    assert out.input_channel == 7


def test_output_eq_and_balance_events():
    out = Output(1)
    out._process_event(["EQ", "3"])
    out._process_event(["BAL", "10"])
    assert out._eq == 3
    assert out._balance == 10


@pytest.mark.parametrize("parts", [
    ["VOL", "TIMEOUT"],
    ["AS", "INX"],
    ["EQ", "?"],
    ["BAL", ""],
])
def test_output_non_integer_values_are_ignored(parts):
    out = Output(1)
    out._process_event(["VOL", "20"])
    out._process_event(["AS", "IN2"])
    out._process_event(parts)
    assert out.volume == 20
    assert out.input_channel == 2


def test_output_unknown_event_is_ignored():
    out = Output(1)
    out._process_event(["FOLLOW", "1"])
    assert str(out)  # still in a sane state


# --- Input event parsing ---

def test_input_volume_event():
    inp = Input(2)
    inp._process_event(["VOL", "15"])
    assert inp.volume == 15


@pytest.mark.parametrize("parts", [["MUTE"], ["AS", "IN1"]])
def test_input_rejects_output_only_events(parts):
    with pytest.raises(Exception, match="Should not be called"):
        Input(2)._process_event(parts)


def test_input_sig_sta_non_integer_is_ignored():
    inp = Input(2)
    inp._process_event(["SIG", "STA", "3"])
    inp._process_event(["SIG", "STA", "TIMEOUT"])
    assert inp.signal_status == 3
