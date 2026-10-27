"""REC Remote wire facts. See docs/protocol.md for evidence and boundaries."""
from dataclasses import dataclass
import struct

SERVICE_UUID = "84c6978c-deb9-4073-8c9d-d7bf48790ef7"
DATA, ACK, SHOT = 0x09, 0x01, 0x19
MAX_PAYLOAD = 65536


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class Frame:
    kind: int
    sequence: int
    payload: bytes = b""


def encode(frame: Frame) -> bytes:
    if frame.kind not in (DATA, ACK, SHOT):
        raise ProtocolError("Unsupported frame type")
    if not 0 <= frame.sequence <= 255 or len(frame.payload) > MAX_PAYLOAD:
        raise ProtocolError("Invalid sequence or oversized payload")
    if frame.kind == ACK and frame.payload:
        raise ProtocolError("ACK must have an empty payload")
    body = struct.pack(">BBI", frame.kind, frame.sequence, len(frame.payload)) + frame.payload
    body += bytes([sum(body) & 255])
    escaped = bytearray([0x3E])
    for value in body:
        if value in (0x3C, 0x3D, 0x3E):
            escaped.extend((0x3D, value & 0xEF))
        else:
            escaped.append(value)
    escaped.append(0x3C)
    return bytes(escaped)


def decode(wire: bytes) -> Frame:
    if len(wire) < 9 or wire[0] != 0x3E or wire[-1] != 0x3C:
        raise ProtocolError("Missing frame delimiters")
    body = bytearray()
    escaped = False
    for value in wire[1:-1]:
        if escaped:
            if value not in (0x2C, 0x2D, 0x2E):
                raise ProtocolError("Invalid escape")
            body.append(value | 0x10)
            escaped = False
        elif value == 0x3D:
            escaped = True
        elif value in (0x3C, 0x3E):
            raise ProtocolError("Unexpected delimiter")
        else:
            body.append(value)
    if escaped or len(body) < 7:
        raise ProtocolError("Incomplete frame")
    if sum(body[:-1]) & 255 != body[-1]:
        raise ProtocolError("Checksum mismatch")
    kind, sequence, length = struct.unpack(">BBI", body[:6])
    if length > MAX_PAYLOAD or length != len(body) - 7:
        raise ProtocolError("Payload length mismatch")
    if kind not in (DATA, ACK, SHOT) or (kind == ACK and length):
        raise ProtocolError("Invalid frame type or ACK length")
    if kind in (DATA, ACK) and sequence not in (0, 1):
        raise ProtocolError("Reliable sequence must be 0 or 1")
    return Frame(kind, sequence, bytes(body[6:-1]))


class FrameParser:
    """Accept arbitrary RFCOMM chunks; resynchronize after corrupt frames."""

    def __init__(self):
        self.buffer = bytearray()
        self.errors: list[str] = []

    def feed(self, chunk: bytes) -> list[Frame]:
        frames = []
        for value in chunk:
            if value == 0x3E:
                self.buffer = bytearray([value])
            elif self.buffer:
                self.buffer.append(value)
                if value == 0x3C:
                    try:
                        frames.append(decode(bytes(self.buffer)))
                    except ProtocolError as exc:
                        self.errors.append(str(exc))
                    self.buffer.clear()
                elif len(self.buffer) > 2 * (MAX_PAYLOAD + 7) + 2:
                    self.errors.append("Frame exceeds size limit")
                    self.buffer.clear()
        return frames


def ack_for(frame: Frame) -> Frame:
    if frame.kind != DATA:
        raise ProtocolError("Only reliable data is acknowledged")
    return Frame(ACK, 1 - frame.sequence)


def key(code: int) -> bytes:
    if not 1 <= code <= 8:
        raise ValueError("Key must be 1..8")
    return bytes([0x10, code])


def track_mark(name: str = "Mark", kind: int = 1) -> bytes:
    label = name.encode("utf-8")
    if type(kind) is not int or kind not in (1, 2, 3, 4) or not 1 <= len(label) <= 128:
        raise ValueError("Mark type must be 1..4 and name must contain 1..128 UTF-8 bytes")
    return bytes([0x11, 6, 3]) + struct.pack(">H", kind) + bytes([len(label)]) + label


def set_gain(value: int) -> bytes:
    if not 0 <= value <= 30:
        raise ValueError("PCM-A10 recording gain must be 0..30")
    return bytes([0x15, 6, 4]) + struct.pack(">H", value)


def get_item(item: int) -> bytes:
    if type(item) is not int or not 0 <= item <= 0xFFFF:
        raise ValueError("Invalid setting ID")
    return b"\x17" + struct.pack(">H", item)


def set_item(item: int, value: int) -> bytes:
    if type(item) is not int or type(value) is not int or not 0 <= item <= 0xFFFF or not 0 <= value <= 0xFFFF:
        raise ValueError("Invalid setting ID or value")
    return b"\x15" + struct.pack(">HH", item, value)


def parse_mark(payload: bytes) -> dict | None:
    if len(payload) < 3 or payload[0] not in (0x12, 0x14):
        raise ProtocolError("Invalid track mark response")
    if payload[1] or payload[2] == 0:
        return None
    if len(payload) < 12 or payload[2] != 1 or payload[3:5] != b"\x06\x03":
        raise ProtocolError("Invalid track mark item")
    kind = int.from_bytes(payload[5:7], "big")
    hours, minutes, seconds = map(bcd, payload[7:10])
    number, length = payload[10:12]
    if kind not in (1, 2, 3, 4) or not 1 <= number <= 98 or minutes >= 60 or seconds >= 60 or not 1 <= length <= 128 or len(payload) != 12 + length:
        raise ProtocolError("Invalid track mark data")
    return {"type": kind, "number": number, "name": payload[12:].decode("utf-8", "replace"),
            "time": f"{hours:02d}:{minutes:02d}:{seconds:02d}"}


def parse_filename(payload: bytes) -> str | None:
    if len(payload) < 3 or payload[0] != 0x34:
        raise ProtocolError("Invalid filename response")
    if payload[1] or not payload[2]:
        return None
    if len(payload) < 6 or payload[2] != 1 or payload[3:5] != b"\x07\x08" or not 1 <= payload[5] <= 128 or len(payload) != 6 + payload[5]:
        raise ProtocolError("Invalid filename data")
    return payload[6:].decode("utf-8", "replace")


def meter_level(whole: int, fraction: int) -> float | None:
    if whole == fraction == 255:
        return None
    if fraction > 99:
        raise ProtocolError("Invalid meter fraction")
    # Java byte is signed. The recorder uses a biased signed whole part.
    signed = whole - 256 if whole >= 128 else whole
    integer = 64 - signed
    return integer + (fraction / 100 if integer >= 0 else -fraction / 100)


def parse_hello(payload: bytes) -> dict:
    if len(payload) < 7 or payload[0] != 0:
        raise ProtocolError("Invalid device greeting")
    version = int.from_bytes(payload[1:3], "big")
    offset = 5 if version >= 0x3000 else 4
    model_type = payload[4] if version >= 0x3000 else 1
    if offset >= len(payload):
        raise ProtocolError("Truncated greeting")
    model_size = payload[offset]
    offset += 1
    if offset + model_size >= len(payload):
        raise ProtocolError("Truncated model name")
    model = payload[offset:offset + model_size].decode("utf-8", "replace")
    offset += model_size
    uuid_size = payload[offset]
    offset += 1
    if offset + uuid_size > len(payload):
        raise ProtocolError("Truncated device identifier")
    # The recorder's unique identifier is deliberately not copied into public reports.
    return {"protocol": version, "capability_version": payload[3],
            "model_type": model_type, "model": model}


def parse_capabilities(payload: bytes) -> dict[int, dict]:
    if len(payload) < 2 or payload[0] != 3:
        raise ProtocolError("Invalid capabilities response")
    items = {}
    offset = 2
    for _ in range(payload[1]):
        if offset + 4 > len(payload):
            raise ProtocolError("Truncated capability")
        item = int.from_bytes(payload[offset:offset + 2], "big")
        info, value = payload[offset + 2:offset + 4]
        offset += 4
        values = []
        if info == 1:
            if offset + 2 * value > len(payload):
                raise ProtocolError("Truncated capability values")
            values = [int.from_bytes(payload[i:i + 2], "big")
                      for i in range(offset, offset + 2 * value, 2)]
            offset += 2 * value
        items[item] = {"type": info, "value": value, "values": values}
    if offset != len(payload):
        raise ProtocolError("Unexpected capability data")
    return items


def parse_items(payload: bytes) -> tuple[int, dict[int, int]]:
    if len(payload) < 3:
        raise ProtocolError("Truncated item response")
    count = payload[2]
    if len(payload) != 3 + count * 4:
        raise ProtocolError("Invalid item response length")
    return payload[1], {
        int.from_bytes(payload[i:i + 2], "big"): int.from_bytes(payload[i + 2:i + 4], "big")
        for i in range(3, len(payload), 4)
    }


def bcd(value: int) -> int:
    if (value >> 4) > 9 or value & 15 > 9:
        raise ProtocolError("Invalid BCD time")
    return (value >> 4) * 10 + (value & 15)
