import ctypes
import unittest
from pcma10_remote.bluetooth_windows import GUID, SOCKADDR_BTH, DEVICE_INFO, address_number, address_text


class WindowsAbiTests(unittest.TestCase):
    def test_sdk_structure_layout(self):
        self.assertEqual(ctypes.sizeof(GUID), 16)
        self.assertEqual(ctypes.sizeof(SOCKADDR_BTH), 30)
        self.assertEqual(SOCKADDR_BTH.btAddr.offset, 2)
        self.assertEqual(SOCKADDR_BTH.serviceClassId.offset, 10)
        self.assertEqual(SOCKADDR_BTH.port.offset, 26)
        self.assertEqual(ctypes.sizeof(DEVICE_INFO), 560)

    def test_address_validation_and_roundtrip(self):
        self.assertEqual(address_text(address_number("AA:BB:CC:DD:EE:FF")), "AA:BB:CC:DD:EE:FF")
        for address in ("", "AA:BB", "zz:zz:zz:zz:zz:zz", "1" * 13):
            with self.assertRaises(ValueError):
                address_number(address)
