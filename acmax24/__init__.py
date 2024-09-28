import time, threading
import asyncio
import copy
import json
import requests
import websockets
import logging
import threading

INPUT_MAX = 24
OUTPUT_MAX = 24
REFRESH_INTERVAL = 60

LOG: logging.Logger = logging.getLogger(__package__)

# InputOutput is the base class for all Inputs and Outputs; common properties live in here
class InputOutput:
    def __init__(self, index: int):
        self._index: int = index
        self._enabled: bool = False
        self._label: str = "unset"
        self._volume: int = -1

    @property
    def label(self) -> str:
        """What is the label for this Input/Output?"""
        return self._label

    @property
    def enabled(self) -> bool:
        """Is this Input/Output Enabled?"""
        return self._enabled

    @property
    def index(self) -> int:
        """What is the index of this Input/Output"""
        return self._index

    @property
    def volume(self) -> int:
        """What is the volume of this Input/Output"""
        return self._volume

    def _process_event(self, parts: "list[str]"):
        if parts[0] == "EN":
            self._enabled = True
        elif parts[0] == "DIS":
            self._enabled = False
        elif parts[0] == "VOL":
            if "LOCK" not in parts[1]:
                self._volume = int(parts[1])
            else:
        # The following only occur for outputs.......
                self._set_volume_lock(not ("UNLOCK" in parts[1]))
        elif parts[0] == "MUTE":
            self._set_muted(True)
        elif parts[0] == "UNMUTE":
            self._set_muted(False)
        elif parts[0] == "AS":
            self._set_input_channel(int(parts[1].strip("IN")))
        elif parts[0] == 'EQ':
            self._set_eq(int(parts[1]))
        elif parts[0] == 'BAL':
            self._set_balance(int(parts[1]))
        else:
            #LOG.debug('Ignoring update: %s', str(parts))
            pass

        if self.enabled:
            LOG.debug(f"IO {self} ===> Processed event {parts}")


    def _set_input_channel(self, idx: int):
        raise Exception("Should not be called")

    def _set_muted(self, muted: bool):
        raise Exception("Should not be called")
    
    def _set_eq(self, idx: int):
        raise Exception("Should not be called")

    def _set_balance(self, balance: int):
        raise Exception("Should not be called")

    def _set_volume_lock(self, locked: bool):
        raise Exception("Should not be called")

    def __str__(self) -> str:
        return "Label=" + self._label + ", Enabled=" + str(self.enabled) + ", Volume=" + str(self._volume)


class Output(InputOutput):
    def __init__(self, index: int):
        super().__init__(index)
        self._muted: bool = False
        self._eq: int = -1
        self._balance: int = -1
        self._input_channel = -1
        self._volume_lock: bool = False
        pass

    @property
    def muted(self) -> bool:
        """Is this Output Muted?"""
        return self._muted

    @property
    def input_channel(self) -> int:
        """Which input is this output mapped to?"""
        return self._input_channel

    def _set_input_channel(self, idx: int):
        self._input_channel = idx

    def _set_muted(self, muted: bool):
        self._muted = muted
    
    def _set_eq(self, eq: int):
        self._eq = eq
    
    def _set_balance(self, balance: int):
        self._balance = balance
    
    def _set_volume_lock(self, locked: bool):
        self._volume_lock = locked
    
    def __str__(self) -> str:
        return super().__str__() + ", Muted=" + str(self.muted) + ", InputChannel=" + str(self.input_channel) + \
          ", Balance=" + str(self._balance) + ", EQ=" + str(self._eq) + ", VolumeLock=" + str(self._volume_lock)


class Input(InputOutput):
    def __init__(self, index: int):
        super().__init__(index)
    
# Transport is responsible for communicating with the AC-MAX-24 API.  The idea here
# is that we can drop in another Transport for testing purposes, though there are
# no tests right now.  The Home Assistant event/threading model isn't entirely clear
# to me, so I've opted to spawn a thread here, which is responsible for maintaining the
# websocket connection and processing the events.
class Transport:
    def __init__(self, hostname, callback):
        self.socket = None
        self._hostname = hostname
        self._callback = callback

    async def start(self):
        LOG.debug("Spawning transport thread")
        self._thread = threading.Thread(target=self.transport_thread)
        self._thread.setDaemon(True)
        self._thread.start()
        LOG.debug("Spawned transport thread")

    def transport_thread(self):
        LOG.debug("Transport thread pre")
        asyncio.run(self.transport_thread_runloop())
        LOG.debug("Transport thread post")

    async def transport_thread_runloop(self):
        LOG.debug("Transport thread started")
        LOG.debug(f"Establishing websocket connection to {self._hostname}")
        self._refresh_task = asyncio.create_task(self.refresh())

        async for websocket in websockets.connect(
            uri=f"ws://{self._hostname}/ws/uart"
        ):
            self.socket = websocket
            # For some reason, one request isn't always enough, the first will often return 'CMD ERROR'
            await self.refresh()           
            await self.refresh()           
            try:
                async for message in websocket:
                    await self.process(message)
            except websockets.ConnectionClosed:
                LOG.warn("websocket closed, connect will automatically restablish")
                continue
            except Exception as e:
                LOG.error(f"received error: {e}; ignoring")
                continue

    def stop(self):
        self._refresh_task.cancel()
        
    async def refresh_task(self):
        """This is used to periodically pull all state from the device, to reconcile and catch any changes we missed"""
        while True:
            await asyncio.sleep(REFRESH_INTERVAL)
            await self.refresh()

    async def refresh(self):
        try:
            await self.send("GET CONFIG\r")
        except Exception:
            pass

    def send(self, message: str):
        if self.socket:
            return self.socket.send(message)
        else:
            LOG.warn("Cannot send '%s' as socket not connected", message.strip("\r").strip("\n"))
            raise Exception("cannot send '%s', as socket not connected", message)

    async def process(self, message):
        # print("Received: " + message)
        if self._callback:
            await self._callback(message)


class ACMax24:
    def __init__(self, hostname, notify_callback):
        # Note, to make array indexing clean, we create 0 index objects which don't really exist in the matrix.
        self._inputs = [Input(idx) for idx in range(0, INPUT_MAX + 1)]
        self._outputs = [Output(idx) for idx in range(0, OUTPUT_MAX + 1)]
        self._hostname = hostname
        self._transport = Transport(hostname, self._process_event)
        self._initial_io_config_received: bool = False
        self._initial_labels_fetched: bool = False
        self._notify_callback = notify_callback

    async def start(self):
        """Start sets up the async/background tasks"""
        LOG.debug("Starting transport")
        await self._transport.start()
        LOG.debug("Start complete")
    

    async def wait_for_initial_state(self, timeout: int) -> bool:
        """Wait upto {timeout} seconds, returns True if the labels and io config is loaded"""
        iterations = 0
        while True:
            if self._initial_io_config_received and self._initial_labels_fetched:
                return True
            elif iterations > timeout:
                LOG.debug("timed out waiting for initial state")
                return False
            else:
                LOG.debug("_initial_io_config_received = %s, _initial_labels_fetched = %s, sleeping...", 
                          self._initial_io_config_received,
                          self._initial_labels_fetched)

            await asyncio.sleep(1)
            iterations = iterations + 1

    def update(self):
            try:
                resp = requests.get(f"http://{self._hostname}/do?cmd=status")
                if resp.status_code == 200:
                    LOG.debug("Updating labels following successful fetch")
                    portalias = json.loads(resp.json()['info']['portalias'])
                    for input_alias in portalias['inputsID']:
                        idx = int(input_alias['port'].strip("IN "))
                        self._inputs[idx]._label = input_alias['id']
                    for output_alias in portalias['outputsAudioID']:
                        idx = int(output_alias['port'].strip("OUT "))
                        self._outputs[idx]._label = output_alias['id']
                    if not self._initial_labels_fetched:    
                        LOG.info("Initial label fetch completed")
                        self._initial_labels_fetched = True
                else:
                    LOG.warn("Failed to fetch status from matrix, got response code %d", resp.status_code)
            except Exception as e:
                LOG.warn("Failed to fetch status from matrix, got exception: %s", e)

    def stop(self):
        self._refresh_task.cancel()
        self._transport.stop()

    async def change_input_for_output(self, output_idx: int, input_idx: int):
        """Map the given output, to the given input"""
        # Lookup the input to trigger the validation
        self.get_input(input_idx)
        await self._send_output_cmd(output_idx, f'AS IN{input_idx}')
    
    async def mute_output(self, output_idx: int, muted: bool = False):
        await self._send_output_cmd(output_idx, "MUTE" if muted else "UNMUTE")

    async def step_output_volume(self, output_idx: int, step: int = 1):
        adjust_str = "+" if step > 0 else "-"
        # FIXME: Because of the 'CMD ERROR' bug, we send all commands twice -- but
        # for a volume step adjustment, which isn't idempotent, that can be problematic.
        # *Most* of the time, the first one fails with 'CMD ERROR' -- but it can also
        # succeed, leaving us two double the volume adjustment.
        await self._send_output_cmd(output_idx, f"VOL {adjust_str} {abs(step)}")

    async def set_output_volume(self, output_idx: int, volume: int):
        await self._send_output_cmd(output_idx, f"VOL {volume}")

    async def _send_output_cmd(self, output_idx: int, cmd_str: str):
        # TODO:FIXME I'm not 100% sure of the correct sequence of \r\n's -- it seems no matter
        # what I try, I get sporadic 'CMD ERROR' responses
        # To avoid that manifesting as a bug, we send every request twice, which always
        # seems to result in one of them working.  The AC Max Pro UI doesn't run into
        # this issue....

        # All we need to do here is issue the command; if it's successful, we'll pickup
        # that state change back through the websocket.

        # Lookup the output to trigger output validation
        self.get_output(output_idx)
        for x in range(0, 2):
            try:
                cmd = f'SET OUT{output_idx} ' + cmd_str + '\r\n'
                LOG.debug("Sending: '%s'", cmd)
                await self._transport.send(cmd)
            except Exception as e:
                LOG.warn("Exception when sending command. cmd='%s', exception='%s'", cmd, e)
                pass


    def get_enabled_inputs(self) -> "set[Input]":
        """Return a set of all enabled Inputs"""
        return set([input for input in self._inputs if input.enabled])

    def get_enabled_outputs(self) -> "set[Output]":
        """Return a set of all enabled Outputs"""
        return set([output for output in self._outputs if output.enabled])

    def get_input(self, idx: int) -> Input:
        if idx < 1 or idx > INPUT_MAX:
            raise IndexError
        return self._inputs[idx]
    
    def get_output(self, idx: int) -> Output:
        if idx < 1 or idx > OUTPUT_MAX:
            raise IndexError
        return self._outputs[idx]
    
    def _get_io(self, io: str) -> InputOutput:
        """Utility function to convert from INxxx or OUTyyy to the corresponding Input or Output"""
        idx = int(io.strip("IN").strip("OUT"))
        if io.startswith("IN"):
            return self._inputs[idx]
        elif io.startswith("OUT"):
            return self._outputs[idx]
        else:
            raise Exception("couldn't parse %s", io)

    async def _process_event(self, msg: str):
        """Process updates from the uart websocket"""
        parts = msg.strip("\r\n").split(" ")
        # We don't do anything with these types of event
        ignored_update_types = ['TRIGGER', 'RIP', 'HIP', 'NMK', 'TIP', 'FOLLOW', 'SIG', 'ADDR', 'BAUDR']
        updated_io: InputOutput = None
        if len(parts) > 2 and parts[0] == "SET":
            if (parts[1].startswith("IN") or parts[1].startswith("OUT")):
                updated_io = self._get_io(parts[1])
                updated_io._process_event(parts[2:])
            elif parts[1] in ignored_update_types:
                pass
            elif parts[1] == "DHCP":
                # This is the last thing we receive in the full config update.
                if not self._initial_io_config_received:
                    LOG.info("Initial IO config received")
                    self._initial_io_config_received = True
            elif "EQ" in parts[1]:
                pass
            else:
                LOG.warn("Unrecognized SET update: %s", msg.strip("\r\n"))
        if len(parts) > 1 and parts[0].startswith('OUT'):
            # For some reason, in addition to 'SET OUTxx AS INyyy', the API also sends those updates without
            # the 'SET' prefix; and we handle those here.
            updated_io = self._get_io(parts[0])
            updated_io._process_event(parts[1:])
        elif parts[0] == 'CMD' and parts[1] == 'ERROR':
            # The API has some quirks and sends this a lot, even for seemingly valid commands
            LOG.debug("Received CMD ERROR")
        else:
            #LOG.debug("Ignoring event: %s", msg.strip("\r\n"))
            pass

        if updated_io and self._initial_io_config_received:
            LOG.debug("Triggering notify callback following change to IO: %s", updated_io)
            await self._notify_callback()
            pass


    def __str__(self) -> str:
        res = ""
        for input in self._inputs:
            if input._enabled:
                res += "IN" + str(input.index) + ": " + str(input) + "\n"

        for output in self._outputs:
            if output._enabled:
                res += "OUT" + str(output.index) + ": " + str(output) + "\n"

        return res
    
    async def save_state(self):
        """Returns a dictionary containing the current state of the inputs/outputs, which can be restored
        using restore_state()"""
        state = {}
        state['inputs'] = copy.deepcopy(self._inputs)
        state['outputs'] = copy.deepcopy(self._outputs)
        return state
    
    async def restore_state(self, state):
        """Takes a state dump, as returned by save_state(), and makes the system match the supplied state"""
        saved_input: Input
        for saved_input in state['inputs']:
            # Nothing to implement for inputs right now...
            pass

        saved_output: Output
        for saved_output in state['outputs']:
            LOG.debug("Restoring saved output: %s", saved_output)
            # We assume that enabled/disabled isn't changed between save/restore (since we don't support that)
            if saved_output.enabled:
                await self.mute_output(saved_output.index, saved_output.muted)
                await self.set_output_volume(saved_output.index, saved_output.volume)
                await self.change_input_for_output(saved_output.index, saved_output.input_channel)