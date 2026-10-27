"""Deterministic protocol peer for offline tests and the explicitly labelled demo."""
import math
import queue
import struct
import threading
import time

from .protocol import ACK, DATA, SHOT, Frame, FrameParser, ack_for, encode
from .settings import BY_KEY, SWITCH


def a10_capabilities():
    """Capability values observed on the user's A10; no device identifiers."""
    enums = {0x0101: [2, 13, 14, 15, 3, 4, 5, 6, 7], 0x0102: [2, 13, 14, 15, 3, 4, 5, 6, 7],
             0x0103: [2, 13, 14, 15, 3, 4, 5, 6, 7], 0x0201: list(range(1, 9)),
             0x0202: list(range(1, 9)), 0x0203: [1, 3, 4], 0x0204: [1, 3, 4],
             0x0301: [1, 2], 0x0401: [1, 3, 4], 0x0403: list(range(1, 11)), 0x0602: [1, 2], 0x0605: []}
    caps = {item: {"type": 1, "value": len(values), "values": values} for item, values in enums.items()}
    for item, value in {0x0402: 1, 0x0502: 1, 0x0501: 1, 0x0503: 1, 0x0504: 0,
                        0x0507: 1, 0x0505: 13, 0x0506: 0, 0x0509: 1}.items():
        caps[item] = {"type": 2, "value": value, "values": []}
    return caps


def _items(command, items, status=0):
    body = bytearray([command, status, len(items)])
    for item, value in items.items():
        body.extend(struct.pack(">HH", item, value))
    return bytes(body)


def _bcd(value):
    return (value // 10) << 4 | (value % 10)


class SimulatedTransport:
    def __init__(self, *, model="PCM-A10", drop_action_ack=False, capabilities=None, reject_settings=None, ignore_settings=()):
        self.model = model
        self.drop_action_ack = drop_action_ack
        self.incoming = queue.Queue()
        self.parser = FrameParser()
        self.sequence = 0
        self.last_host_sequence = None
        self.closed = threading.Event()
        self.lock = threading.RLock()
        self.status = 1
        self.gain = 15
        self.capabilities = a10_capabilities() if capabilities is None else capabilities
        self.reject_settings = reject_settings or {}
        self.ignore_settings = set(ignore_settings)
        self.values = {setting.item: next(iter(setting.choices)) for setting in BY_KEY.values()}
        self.values.update({0x0101: 6, 0x0201: 8, 0x0202: 8, 0x0203: 4,
                            **{setting.item: 2 for setting in BY_KEY.values() if setting.choices == SWITCH}})
        self.presets = {}
        self.rehearsal_status = 4
        self.input_status = 4
        self.last_mark = None
        self.marks = 0
        self.elapsed = 0.0
        self.actions: list[bytes] = []
        self.host_frames: list[Frame] = []
        self._publish(b"\x00\x30\x00\x03\x01" + bytes([len(model)]) + model.encode() + b"\x04DEMO")
        self.thread = threading.Thread(target=self._tick, name="pcm-a10-demo", daemon=True)
        self.thread.start()

    def _publish(self, payload, kind=DATA):
        with self.lock:
            sequence = self.sequence if kind == DATA else 255
            if kind == DATA:
                self.sequence = 1 - self.sequence
            self._wire(encode(Frame(kind, sequence, payload)))

    def _wire(self, wire):
        # Fragments deliberately cut across header, payload and escape sequences.
        self.incoming.put(wire[:3])
        self.incoming.put(wire[3:7])
        self.incoming.put(wire[7:])

    def sendall(self, data):
        if self.closed.is_set():
            raise ConnectionError("Demo transport closed")
        with self.lock:
            for frame in self.parser.feed(data):
                self.host_frames.append(frame)
                if frame.kind == ACK:
                    continue
                is_action = bool(frame.payload) and frame.payload[0] in (0x10, 0x11, 0x15)
                if not (is_action and self.drop_action_ack):
                    self._wire(encode(ack_for(frame)))
                if frame.sequence == self.last_host_sequence:
                    continue
                self.last_host_sequence = frame.sequence
                self._command(frame.payload)

    def _command(self, payload):
        command = payload[0]
        if command == 1:
            return
        if command == 2:
            body = bytearray([3, len(self.capabilities)])
            for item, cap in self.capabilities.items():
                body.extend(struct.pack(">HBB", item, cap["type"], cap["value"]))
                if cap["type"] == 1:
                    for value in cap["values"]:
                        body.extend(struct.pack(">H", value))
            self._publish(bytes(body))
        elif command == 0x20:
            self._publish(_items(0x21, {0x0704: self.status, 0x070B: self.input_status, 0x070E: self.rehearsal_status}))
        elif command == 0x23:
            self._publish(_items(0x24, {0x0701: 5, 0x0703: 1, 0x070A: 2, 0x070C: 0xFFFF}))
        elif command == 0x17:
            item = int.from_bytes(payload[1:3], "big")
            self._publish(_items(0x18, {item: self.gain if item == 0x0604 else self.values.get(item, 0)}))
        elif command == 0x30:
            self._time(payload[2])
        elif command == 0x10:
            self.actions.append(payload)
            code = payload[1]
            if code == 1:
                self.status = 1
                self.elapsed = 0
                self.rehearsal_status = 4
            elif code == 2:
                self.status = 2
            elif code == 3:
                self.status = 3
                self.rehearsal_status = 4
            elif code == 4:
                self.gain = min(30, self.gain + 1)
                self._publish(_items(0x16, {0x0604: self.gain}))
            elif code == 5:
                self.gain = max(0, self.gain - 1)
                self._publish(_items(0x16, {0x0604: self.gain}))
            elif code == 6:
                self.status = 2
                self.rehearsal_status = 2
            elif code == 7:
                self.status = 2
                self.rehearsal_status = 4
            self._publish(_items(0x22, {0x0704: self.status, 0x070E: self.rehearsal_status}))
        elif command == 0x15:
            self.actions.append(payload)
            item, value = struct.unpack(">HH", payload[1:5])
            result = self.reject_settings.get(item, 0)
            if result:
                self._publish(_items(0x16, {item: self.values.get(item, 0)}, result))
                return
            if item not in self.ignore_settings:
                if item == 0x0604:
                    self.gain = value
                elif item == 0x0602:
                    self.presets[value] = self.values.copy()
                else:
                    self.values[item] = value
                    if item == 0x0201:
                        self.rehearsal_status = 4 if value == 8 else 3
                    if item == 0x0403 and value in (9, 10) and value - 8 in self.presets:
                        self.values.update(self.presets[value - 8])
                        self.values[item] = value
            actual = self.gain if item == 0x0604 else value if item == 0x0602 else self.values[item]
            self._publish(_items(0x16, {item: actual}))
        elif command == 0x11:
            self.actions.append(payload)
            self.marks += 1
            kind, length = int.from_bytes(payload[3:5], "big"), payload[5]
            label = payload[6:6 + length]
            total = int(self.elapsed)
            mark = b"\x00\x01\x06\x03" + kind.to_bytes(2, "big") + bytes([_bcd(total // 3600), _bcd(total // 60 % 60), _bcd(total % 60), self.marks, length]) + label
            self.last_mark = mark
            self._publish(b"\x12" + mark)
        elif command == 0x13:
            self._publish(b"\x14" + (self.last_mark or b"\x00\x00"))
        elif command == 0x33:
            name = b"DEMO_001.WAV"
            self._publish(b"\x34\x00\x01\x07\x08" + bytes([len(name)]) + name)
        elif command == 0x35:
            self._publish(bytes.fromhex("36 00 01 07 0d 4c 00 4e 00 00 00"))

    def _time(self, item):
        total = int(self.elapsed) if item == 6 else 8 * 3600
        hours, rest = divmod(total, 3600)
        minutes, seconds = divmod(rest, 60)
        self._publish(bytes([0x31, 0, 1, 7, item, hours // 100,
                             _bcd(hours % 100), _bcd(minutes), _bcd(seconds)]))

    def _tick(self):
        last = time.monotonic()
        while not self.closed.wait(0.2):
            now = time.monotonic()
            with self.lock:
                if self.status == 3:
                    self.elapsed += now - last
                if self.status in (2, 3):
                    left = int(2200 + 1000 * math.sin(now * 2))
                    right = int(2600 + 800 * math.cos(now * 2.3))
                    record = bytes([1, left // 100, left % 100, right // 100, right % 100, 0, 0])
                    record += int(self.elapsed * 1000).to_bytes(4, "big")
                    self._publish(b"\x40\x01" + record, SHOT)
                    self._time(6)
            last = now

    def recv(self, size=4096):
        try:
            return self.incoming.get(timeout=0.2)
        except queue.Empty:
            if self.closed.is_set():
                return b""
            raise TimeoutError from None

    def close(self):
        self.closed.set()
        self.incoming.put(b"")
