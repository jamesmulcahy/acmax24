import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest
from acmax24 import INPUT_MAX, OUTPUT_MAX, ACMax24, Transport


# --- Lookups ---

@pytest.mark.parametrize("idx", [0, -1, INPUT_MAX + 1])
def test_get_input_out_of_range(matrix, idx):
    with pytest.raises(IndexError):
        matrix.get_input(idx)


@pytest.mark.parametrize("idx", [0, -1, OUTPUT_MAX + 1])
def test_get_output_out_of_range(matrix, idx):
    with pytest.raises(IndexError):
        matrix.get_output(idx)


def test_get_input_output_bounds_inclusive(matrix):
    assert matrix.get_input(1).index == 1
    assert matrix.get_input(INPUT_MAX).index == INPUT_MAX
    assert matrix.get_output(1).index == 1
    assert matrix.get_output(OUTPUT_MAX).index == OUTPUT_MAX


@pytest.mark.asyncio
async def test_get_enabled_inputs_and_outputs(matrix):
    await matrix._process_event("SET IN2 EN\r\n")
    await matrix._process_event("SET OUT3 EN\r\n")
    await matrix._process_event("SET OUT4 EN\r\n")
    await matrix._process_event("SET OUT4 DIS\r\n")
    assert {i.index for i in matrix.get_enabled_inputs()} == {2}
    assert {o.index for o in matrix.get_enabled_outputs()} == {3}


# --- _process_event dispatch ---

@pytest.mark.asyncio
async def test_set_out_events_update_output(configured_matrix, notifications):
    await configured_matrix._process_event("SET OUT5 VOL 33\r\n")
    await configured_matrix._process_event("SET OUT5 AS IN9\r\n")
    await configured_matrix._process_event("SET OUT5 MUTE\r\n")
    out = configured_matrix.get_output(5)
    assert (out.volume, out.input_channel, out.muted) == (33, 9, True)
    assert len(notifications) == 3


@pytest.mark.asyncio
async def test_unprefixed_out_event_updates_output(configured_matrix, notifications):
    await configured_matrix._process_event("OUT12 AS IN3\r\n")
    assert configured_matrix.get_output(12).input_channel == 3
    assert len(notifications) == 1


@pytest.mark.asyncio
async def test_two_digit_indices(configured_matrix):
    await configured_matrix._process_event("SET OUT24 AS IN24\r\n")
    await configured_matrix._process_event("SET IN10 VOL 7\r\n")
    assert configured_matrix.get_output(24).input_channel == 24
    assert configured_matrix.get_input(10).volume == 7


@pytest.mark.asyncio
async def test_dhcp_marks_initial_config_received(matrix, notifications):
    await matrix._process_event("SET OUT1 EN\r\n")
    assert matrix._initial_io_config_received is False
    await matrix._process_event("SET DHCP ON\r\n")
    assert matrix._initial_io_config_received is True
    # Events before the initial config completes don't notify
    assert notifications == []
    await matrix._process_event("SET OUT1 VOL 10\r\n")
    assert len(notifications) == 1


@pytest.mark.asyncio
async def test_cmd_error_is_counted(configured_matrix, notifications):
    await configured_matrix._process_event("CMD ERROR\r\n")
    await configured_matrix._process_event("CMD ERROR\r\n")
    assert configured_matrix._errors == 2
    assert notifications == []


@pytest.mark.asyncio
@pytest.mark.parametrize("msg", [
    "SET TRIGGER 1 ON",
    "SET RIP 192.168.1.10",
    "SET SIG STA 1",
    "SET EQ1 FREQ 100",
    "SET SOMETHINGNEW 1",
    "GARBAGE",
    "",
])
async def test_ignored_events_dont_notify_or_raise(configured_matrix, notifications, msg):
    await configured_matrix._process_event(msg + "\r\n")
    assert notifications == []


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, reason="BUG: out-of-range index raises IndexError, which the transport "
                                       "loop treats as a fatal error and reconnects")
async def test_out_of_range_event_is_ignored(configured_matrix):
    await configured_matrix._process_event("SET OUT25 VOL 10\r\n")


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, reason="BUG: bare 'CMD' message raises IndexError on parts[1]")
async def test_bare_cmd_message_is_ignored(configured_matrix):
    await configured_matrix._process_event("CMD\r\n")


# --- Commands ---

@pytest.mark.asyncio
async def test_commands_are_sent_twice_with_expected_format(matrix):
    await matrix.change_input_for_output(3, 7)
    await matrix.mute_output(3, True)
    await matrix.mute_output(3, False)
    await matrix.set_output_volume(3, 55)
    await matrix.step_output_volume(3, 5)
    await matrix.step_output_volume(3, -2)
    assert matrix._transport.sent == [
        "SET OUT3 AS IN7\r\n", "SET OUT3 AS IN7\r\n",
        "SET OUT3 MUTE\r\n", "SET OUT3 MUTE\r\n",
        "SET OUT3 UNMUTE\r\n", "SET OUT3 UNMUTE\r\n",
        "SET OUT3 VOL 55\r\n", "SET OUT3 VOL 55\r\n",
        "SET OUT3 VOL + 5\r\n", "SET OUT3 VOL + 5\r\n",
        "SET OUT3 VOL - 2\r\n", "SET OUT3 VOL - 2\r\n",
    ]


@pytest.mark.asyncio
async def test_change_input_validates_indices(matrix):
    with pytest.raises(IndexError):
        await matrix.change_input_for_output(3, 25)
    with pytest.raises(IndexError):
        await matrix.change_input_for_output(0, 1)
    assert matrix._transport.sent == []


@pytest.mark.asyncio
async def test_send_failure_is_swallowed(matrix):
    async def broken_send(message):
        raise RuntimeError("socket gone")

    matrix._transport.send = broken_send
    await matrix.set_output_volume(1, 10)  # must not raise


# --- Snapshot / restore ---

async def _setup_output(m, idx, volume, input_channel, muted):
    await m._process_event(f"SET OUT{idx} EN\r\n")
    await m._process_event(f"SET OUT{idx} VOL {volume}\r\n")
    await m._process_event(f"SET OUT{idx} AS IN{input_channel}\r\n")
    await m._process_event(f"SET OUT{idx} {'MUTE' if muted else 'UNMUTE'}\r\n")


@pytest.mark.asyncio
async def test_save_state_is_isolated_from_later_changes(matrix):
    await _setup_output(matrix, 1, 40, 2, False)
    state = await matrix.save_state()
    await matrix._process_event("SET OUT1 AS IN5\r\n")
    assert state["outputs"][1].input_channel == 2
    assert matrix.get_output(1).input_channel == 5


@pytest.mark.asyncio
async def test_restore_state_replays_enabled_outputs_only(matrix):
    await _setup_output(matrix, 1, 40, 2, False)
    await _setup_output(matrix, 4, 15, 6, True)
    state = await matrix.save_state()
    matrix._transport.sent.clear()

    await matrix.restore_state(state)

    assert sorted(set(matrix._transport.sent)) == sorted({
        "SET OUT1 UNMUTE\r\n", "SET OUT1 VOL 40\r\n", "SET OUT1 AS IN2\r\n",
        "SET OUT4 MUTE\r\n", "SET OUT4 VOL 15\r\n", "SET OUT4 AS IN6\r\n",
    })


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, reason="BUG: an enabled output with no known input (input_channel=-1) "
                                       "raises IndexError and aborts restoring every later output")
async def test_restore_state_continues_past_unrouted_output(matrix):
    await matrix._process_event("SET OUT1 EN\r\n")  # never received an AS event
    await _setup_output(matrix, 2, 30, 3, False)
    state = await matrix.save_state()
    matrix._transport.sent.clear()

    await matrix.restore_state(state)

    assert "SET OUT2 AS IN3\r\n" in matrix._transport.sent


# --- Initial state / label fetch ---

def _status_response(inputs, outputs, status_code=200):
    resp = MagicMock(status_code=status_code)
    portalias = {
        "inputsID": [{"port": f"IN {i}", "id": label} for i, label in inputs.items()],
        "outputsAudioID": [{"port": f"OUT {o}", "id": label} for o, label in outputs.items()],
    }
    resp.json.return_value = {"info": {"portalias": json.dumps(portalias)}}
    return resp


def test_update_sets_labels(matrix):
    resp = _status_response({1: "Sonos", 12: "TV"}, {3: "Kitchen"})
    with patch("acmax24.requests.get", return_value=resp) as get:
        matrix.update()
    get.assert_called_once_with("http://matrix.invalid/do?cmd=status")
    assert matrix.get_input(1).label == "Sonos"
    assert matrix.get_input(12).label == "TV"
    assert matrix.get_output(3).label == "Kitchen"
    assert matrix._initial_labels_fetched is True


def test_update_non_200_leaves_labels(matrix):
    with patch("acmax24.requests.get", return_value=_status_response({1: "X"}, {}, 500)):
        matrix.update()
    assert matrix.get_input(1).label == "unset"
    assert matrix._initial_labels_fetched is False


def test_update_swallows_exceptions(matrix):
    with patch("acmax24.requests.get", side_effect=ConnectionError("down")):
        matrix.update()
    assert matrix._initial_labels_fetched is False


@pytest.mark.asyncio
async def test_wait_for_initial_state(matrix):
    with patch("acmax24.asyncio.sleep", return_value=None):
        assert await matrix.wait_for_initial_state(2) is False
        matrix._initial_labels_fetched = True
        matrix._initial_io_config_received = True
        assert await matrix.wait_for_initial_state(2) is True


@pytest.mark.xfail(strict=True, reason="BUG: ACMax24.stop() references self._refresh_task, which is never set")
def test_stop(matrix):
    matrix.stop()


# --- Transport ---

def test_transport_send_without_socket_raises():
    t = Transport("matrix.invalid", None)
    with pytest.raises(Exception, match="not connected"):
        t.send("GET CONFIG\r")


@pytest.mark.asyncio
async def test_transport_refresh_swallows_disconnected_errors():
    t = Transport("matrix.invalid", None)
    await t.refresh()
    await t.refresh_signal_status()


@pytest.mark.asyncio
async def test_transport_process_forwards_to_callback():
    received = []

    async def cb(msg):
        received.append(msg)

    await Transport("matrix.invalid", cb).process("SET OUT1 VOL 1\r\n")
    assert received == ["SET OUT1 VOL 1\r\n"]


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, reason="BUG: transport_thread_runloop schedules refresh() once instead of "
                                       "refresh_task(), so the periodic GET CONFIG never runs")
async def test_transport_schedules_periodic_refresh():
    t = Transport("matrix.invalid", None)
    scheduled = []

    def fake_create_task(coro):
        scheduled.append(coro.__qualname__)
        coro.close()
        raise asyncio.CancelledError  # stop before connecting

    with patch("acmax24.asyncio.create_task", side_effect=fake_create_task):
        with pytest.raises(asyncio.CancelledError):
            await t.transport_thread_runloop()
    assert scheduled == ["Transport.refresh_task"]
