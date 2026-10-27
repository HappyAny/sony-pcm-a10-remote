import random
import unittest

from pcma10_remote.protocol import (
    ACK, DATA, SHOT, Frame, FrameParser, ProtocolError, ack_for, bcd, decode,
    encode, key, parse_capabilities, parse_hello, parse_items, set_gain, track_mark,
)


class ProtocolTests(unittest.TestCase):
    def test_reference_vectors(self):
        vectors = [
            (Frame(DATA, 0, b"\x10\x03"), "3e09000000000210031e3c"),
            (Frame(DATA, 1, b"\x10\x02"), "3e09010000000210021e3c"),
            (Frame(DATA, 0, b"\x10\x01"), "3e09000000000210011c3c"),
            (Frame(DATA, 0, b"\x01\x01"), "3e09000000000201010d3c"),
            (Frame(ACK, 1), "3e010100000000023c"),
            (Frame(DATA, 0, bytes.fromhex("3c3d3e")), "3e0900000000033d2c3d2d3d2ec33c"),
        ]
        for frame, wire in vectors:
            with self.subTest(frame=frame):
                self.assertEqual(encode(frame).hex(), wire)
                self.assertEqual(decode(bytes.fromhex(wire)), frame)

    def test_all_bytes_with_arbitrary_fragmentation(self):
        rng = random.Random(2281)
        frames = [Frame(DATA, i % 2, bytes(rng.randrange(256) for _ in range(i))) for i in range(1, 101)]
        wire = b"garbage" + b"".join(map(encode, frames))
        parser, actual = FrameParser(), []
        while wire:
            length = rng.randint(1, 17)
            actual.extend(parser.feed(wire[:length]))
            wire = wire[length:]
        self.assertEqual(actual, frames)
        self.assertFalse(parser.errors)

    def test_corrupt_frame_recovers_at_next_start(self):
        valid = encode(Frame(DATA, 0, key(3)))
        corrupt = bytearray(valid)
        corrupt[-2] ^= 1
        parser = FrameParser()
        self.assertEqual(parser.feed(bytes(corrupt) + valid), [Frame(DATA, 0, key(3))])
        self.assertEqual(parser.errors, ["Checksum mismatch"])

    def test_invalid_length_with_valid_checksum_is_rejected(self):
        body = bytes.fromhex("0900000000031003")
        with self.assertRaisesRegex(ProtocolError, "length"):
            decode(b">" + body + bytes([sum(body) & 255]) + b"<")

    def test_trailing_escape_rejected(self):
        with self.assertRaises(ProtocolError):
            decode(bytes.fromhex("3e0900000000013d3c"))

    def test_ack_complements_sequence(self):
        self.assertEqual(ack_for(Frame(DATA, 0)), Frame(ACK, 1))
        self.assertEqual(ack_for(Frame(DATA, 1)), Frame(ACK, 0))
        with self.assertRaises(ProtocolError):
            ack_for(Frame(SHOT, 255))

    def test_hello_and_capabilities(self):
        hello = parse_hello(b"\x00\x30\x00\x03\x01\x07PCM-A10\x04TEST")
        self.assertEqual((hello["model"], hello["model_type"], hello["protocol"]), ("PCM-A10", 1, 0x3000))
        caps = parse_capabilities(bytes.fromhex("0302060401020000001e06030001"))
        self.assertEqual(caps[0x0604]["values"], [0, 30])
        with self.assertRaises(ProtocolError):
            parse_capabilities(bytes.fromhex("03010604010200"))

    def test_truncated_hello_rejected(self):
        with self.assertRaises(ProtocolError):
            parse_hello(b"\x00\x30\x00\x03\x01\x07PCM")

    def test_track_name_uses_utf8_byte_length(self):
        data = track_mark("测试")
        self.assertEqual(data[:6], bytes.fromhex("110603000106"))
        self.assertEqual(data[6:], "测试".encode())
        with self.assertRaises(ValueError):
            track_mark("a" * 129)

    def test_gain_range(self):
        self.assertEqual(set_gain(30), bytes.fromhex("150604001e"))
        for value in (-1, 31):
            with self.assertRaises(ValueError):
                set_gain(value)

    def test_item_response_length_and_time(self):
        self.assertEqual(parse_items(bytes.fromhex("21000107040003")), (0, {0x0704: 3}))
        with self.assertRaises(ProtocolError):
            parse_items(bytes.fromhex("21000207040003"))
        self.assertEqual(bcd(0x59), 59)
        with self.assertRaises(ProtocolError):
            bcd(0x6A)
