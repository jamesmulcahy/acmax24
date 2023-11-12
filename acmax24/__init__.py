import time, threading
import asyncio
import json
import requests
import websockets
import logging

INPUT_MAX = 24
OUTPUT_MAX = 24
REFRESH_INTERVAL = 60

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
            logging.debug('Ignoring update: %s', str(parts))

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
        self._muted = False
        self._eq: int = -1
        self._balance: int = -1
        self._input_channel = -1
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
    
class Transport:
    def __init__(self, hostname, callback):
        self.socket = None
        self._hostname = hostname
        print("hostname: " + hostname)
        self._callback = callback

    async def start(self):
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
                    # print("got a msh")
                    await self.process(message)
            except websockets.ConnectionClosed:
                continue

    def stop(self):
        self._refresh_task.cancel()
        
    async def refresh_task(self):
        while True:
            await asyncio.sleep(60)  ## TODO: Parameterize
            await self.refresh()

    async def refresh(self):
        await self.send("GET CONFIG\r")

    def send(self, message: str):
        if self.socket:
            return self.socket.send(message)
        else:
            logging.error("Cannot send '%s' as socket not connected", message)
            raise Exception("cannot send '%s', as socket not connected", message)

    async def process(self, message):
        # print("Received: " + message)
        if self._callback:
            self._callback(message)


class ACMax24:
    def __init__(self, hostname):
        # Note, to make array indexing clean, we create 0 index objects which don't really exist in the matrix.
        self._inputs = [Input(idx) for idx in range(0, INPUT_MAX + 1)]
        self._outputs = [Output(idx) for idx in range(0, OUTPUT_MAX + 1)]
        self._hostname = hostname
        self._transport = Transport(hostname, self._process_event)

    async def start(self):
        """Start sets up the async/background tasks"""
        self._refresh_task = asyncio.create_task(self._refresh_labels())
        await self._transport.start()
        await self._refresh_task
        

    def stop(self):
        self._refresh_task.cancel()
        self._transport.stop()

    async def change_input_for_output(self, output_idx: int, input_idx: int):
        """Map the given output, to the given input"""
        # All we need to do here is issue the command; if it's successful, we'll pickup
        # that state change back through the websocket.
        
        # Call these accessors to re-use the index validation
        self.get_input(input_idx)
        self.get_output(output_idx)
        # TODO:FIXME I'm not 100% sure of the correct sequence of \r\n's -- it seems no matter
        # what I try, I get sporadic 'CMD ERROR' responses
        # To avoid that manifesting as a bug, we send every request twice, which always
        # seems to result in one of them working.  The AC Max Pro UI doesn't run into
        # this issue....
        await self._transport.send(f'SET OUT{output_idx} AS IN{input_idx}\r')
        await self._transport.send(f'SET OUT{output_idx} AS IN{input_idx}\r')

    def get_enabled_inputs(self) -> "set[Input]":
        """Return a set of all enabled Inputs"""
        return set([input for input in self._inputs if input.enabled])

    def get_enabled_outputs(self) -> "set[Output]":
        """Return a set of all enabled Outputs"""
        return set([output for output in self._outputs if input.enabled])

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

    async def _refresh_labels(self):
        """Refetch the Input and Output labels from the AX Mac Pro HTTP API"""
        while True:
            try:
                resp = requests.get(f"http://{self._hostname}/do?cmd=status")
                if resp.status_code == 200:
                    portalias = json.loads(resp.json()['info']['portalias'])
                    for input_alias in portalias['inputsID']:
                        idx = int(input_alias['port'].strip("IN "))
                        self._inputs[idx]._label = input_alias['id']
                    for output_alias in portalias['outputsAudioID']:
                        idx = int(output_alias['port'].strip("OUT "))
                        self._outputs[idx]._label = output_alias['id']
                    logging.info('Refreshed labels')
                else:
                    logging.error("Unable to fetch status from api. status_code=%d, response='%s'", str(resp.status_code), resp.text())
            except Exception as e:
                logging.exception("Exception during label refresh")
                pass

            await asyncio.sleep(REFRESH_INTERVAL)

    def _process_event(self, msg: str):
        """Process updates from the uart websocket"""
        # We don't do anything with these types of event
        ignored_update_types = ['TRIGGER', 'RIP', 'HIP', 'NMK', 'TIP', 'DHCP', 'FOLLOW', 'SIG', 'ADDR', 'BAUDR']
        parts = msg.strip("\r\n").split(" ")
        if len(parts) > 2 and parts[0] == "SET":
            if (parts[1].startswith("IN") or parts[1].startswith("OUT")):
                self._get_io(parts[1])._process_event(parts[2:])
            elif parts[1] in ignored_update_types:
                pass
            elif "EQ" in parts[1]:
                pass
            else:
                logging.warn("Unrecognized SET update: %s", msg.strip("\r\n"))
        if len(parts) == 3 and parts[0].startswith('OUT') and parts[1] == 'AS' and parts[2].startswith('IN'):
            # For some reason, in addition to 'SET OUTxx AS INyyy', the API also sends those updates without
            # the 'SET' prefix; and we handle those here.
            self._get_io(parts[0])._process_event(parts[1:])
        elif parts[0] == 'CMD' and parts[1] == 'ERROR':
            # The API has some quirks and sends this a lot, even for seemingly valid commands
            logging.debug("Received CMD ERROR")
        else:
            logging.debug("Ignoring event: %s", msg.strip("\r\n"))

    def __str__(self) -> str:
        res = ""
        for input in self._inputs:
            if input._enabled:
                res += "IN" + str(input.index) + ": " + str(input) + "\n"

        for output in self._outputs:
            if output._enabled:
                res += "OUT" + str(output.index) + ": " + str(output) + "\n"

        return res

    

