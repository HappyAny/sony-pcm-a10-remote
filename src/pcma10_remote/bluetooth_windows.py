"""Windows classic Bluetooth using the documented native APIs, no driver install."""
from dataclasses import dataclass
import ctypes as C
from ctypes import wintypes as W
import os
import re
import select
import socket
import time
import uuid

from .protocol import SERVICE_UUID


@dataclass(frozen=True)
class Device:
    name: str
    address: str
    paired: bool
    connected: bool


def address_number(address: str) -> int:
    if not re.fullmatch(r"(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}|[0-9a-fA-F]{12}", address):
        raise ValueError("Bluetooth address must have six hexadecimal pairs, for example AA:BB:CC:DD:EE:FF")
    return int(address.replace(":", "").replace("-", ""), 16)


def address_text(value: int) -> str:
    text = f"{value:012X}"
    return ":".join(text[i:i + 2] for i in range(0, 12, 2))


class GUID(C.Structure):
    _fields_ = [("Data1", C.c_uint32), ("Data2", C.c_uint16),
                ("Data3", C.c_uint16), ("Data4", C.c_ubyte * 8)]


class SOCKADDR_BTH(C.Structure):
    # ws2bth.h explicitly includes pshpack1.h. Natural alignment is incorrect.
    _pack_ = 1
    _fields_ = [("addressFamily", C.c_uint16), ("btAddr", C.c_uint64),
                ("serviceClassId", GUID), ("port", C.c_uint32)]


class FIND_RADIO_PARAMS(C.Structure):
    _fields_ = [("dwSize", W.DWORD)]


class DEVICE_SEARCH_PARAMS(C.Structure):
    _fields_ = [("dwSize", W.DWORD), ("fReturnAuthenticated", W.BOOL),
                ("fReturnRemembered", W.BOOL), ("fReturnUnknown", W.BOOL),
                ("fReturnConnected", W.BOOL), ("fIssueInquiry", W.BOOL),
                ("cTimeoutMultiplier", C.c_ubyte), ("hRadio", W.HANDLE)]


class SYSTEMTIME(C.Structure):
    _fields_ = [(name, W.WORD) for name in
                ("year", "month", "day_of_week", "day", "hour", "minute", "second", "milliseconds")]


class DEVICE_INFO(C.Structure):
    _fields_ = [("dwSize", W.DWORD), ("Address", C.c_uint64),
                ("ulClassofDevice", W.DWORD), ("fConnected", W.BOOL),
                ("fRemembered", W.BOOL), ("fAuthenticated", W.BOOL),
                ("stLastSeen", SYSTEMTIME), ("stLastUsed", SYSTEMTIME),
                ("szName", W.WCHAR * 248)]


def _dlls():
    if os.name != "nt":
        raise OSError("Live Bluetooth transport requires Windows 10/11. Demo works on other platforms.")
    bth = C.WinDLL("bthprops.cpl", use_last_error=True)
    kernel = C.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "BluetoothFindFirstRadio": ([C.POINTER(FIND_RADIO_PARAMS), C.POINTER(W.HANDLE)], W.HANDLE),
        "BluetoothFindNextRadio": ([W.HANDLE, C.POINTER(W.HANDLE)], W.BOOL),
        "BluetoothFindRadioClose": ([W.HANDLE], W.BOOL),
        "BluetoothFindFirstDevice": ([C.POINTER(DEVICE_SEARCH_PARAMS), C.POINTER(DEVICE_INFO)], W.HANDLE),
        "BluetoothFindNextDevice": ([W.HANDLE, C.POINTER(DEVICE_INFO)], W.BOOL),
        "BluetoothFindDeviceClose": ([W.HANDLE], W.BOOL),
    }
    for name, (args, result) in signatures.items():
        fn = getattr(bth, name)
        fn.argtypes, fn.restype = args, result
    kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = [W.HANDLE], W.BOOL
    return bth, kernel


def radio_count() -> int:
    bth, kernel = _dlls()
    params = FIND_RADIO_PARAMS(C.sizeof(FIND_RADIO_PARAMS))
    handle = W.HANDLE()
    finder = bth.BluetoothFindFirstRadio(C.byref(params), C.byref(handle))
    if not finder:
        error = C.get_last_error()
        if error in (0, 259):
            return 0
        raise C.WinError(error)
    count = 0
    try:
        while True:
            count += 1
            kernel.CloseHandle(handle)
            if not bth.BluetoothFindNextRadio(finder, C.byref(handle)):
                error = C.get_last_error()
                if error not in (0, 259):
                    raise C.WinError(error)
                break
    finally:
        bth.BluetoothFindRadioClose(finder)
    return count


def discover(*, inquiry: bool = False, all_devices: bool = False) -> list[Device]:
    bth, _ = _dlls()
    params = DEVICE_SEARCH_PARAMS()
    params.dwSize = C.sizeof(params)
    params.fReturnAuthenticated = params.fReturnRemembered = params.fReturnConnected = True
    params.fReturnUnknown = inquiry
    params.fIssueInquiry = inquiry
    params.cTimeoutMultiplier = 6 if inquiry else 0  # 6 * 1.28 seconds
    info = DEVICE_INFO()
    info.dwSize = C.sizeof(info)
    finder = bth.BluetoothFindFirstDevice(C.byref(params), C.byref(info))
    if not finder:
        error = C.get_last_error()
        if error in (0, 259):
            return []
        raise C.WinError(error)
    devices = {}
    try:
        while True:
            device = Device(info.szName, address_text(info.Address), bool(info.fAuthenticated), bool(info.fConnected))
            if all_devices or "PCM-A10" in device.name.upper():
                devices[device.address] = device
            info = DEVICE_INFO()
            info.dwSize = C.sizeof(info)
            if not bth.BluetoothFindNextDevice(finder, C.byref(info)):
                error = C.get_last_error()
                if error not in (0, 259):
                    raise C.WinError(error)
                break
    finally:
        bth.BluetoothFindDeviceClose(finder)
    return sorted(devices.values(), key=lambda d: (not d.paired, d.name, d.address))


def doctor() -> dict:
    count = radio_count()
    targets = discover() if count else []
    return {"radio_count": count, "pcm_a10_count": len(targets),
            "pcm_a10_paired_count": sum(d.paired for d in targets),
            "sockaddr_bth_size": C.sizeof(SOCKADDR_BTH),
            "device_info_size": C.sizeof(DEVICE_INFO),
            "transport": "Windows native RFCOMM", "service_uuid": SERVICE_UUID,
            "hardware_control_verified": False}


class WindowsRfcomm:
    def __init__(self, address: str, timeout: float = 12.0, cancel_event=None):
        _dlls()
        target = address_number(address)
        # The caller pairs in Windows Settings; the socket requests authentication.
        ws = C.WinDLL("ws2_32", use_last_error=True)
        ws.socket.argtypes, ws.socket.restype = [C.c_int, C.c_int, C.c_int], C.c_size_t
        ws.connect.argtypes, ws.connect.restype = [C.c_size_t, C.c_void_p, C.c_int], C.c_int
        ws.WSAGetLastError.restype = C.c_int
        handle = ws.socket(32, socket.SOCK_STREAM, 3)
        if handle == C.c_size_t(-1).value:
            raise OSError(ws.WSAGetLastError(), "Could not create a Bluetooth RFCOMM socket")
        # Python's send/recv/timeout work on a Winsock handle. The native connect
        # below supplies the Bluetooth address format that Python does not expose.
        stream = socket.socket(family=32, type=socket.SOCK_STREAM, proto=3, fileno=handle)
        try:
            stream.setsockopt(3, -2147483647, 1)  # SO_BTH_AUTHENTICATE
            stream.setblocking(False)
            sockaddr = SOCKADDR_BTH()
            sockaddr.addressFamily = 32
            sockaddr.btAddr = target
            sockaddr.serviceClassId = GUID.from_buffer_copy(uuid.UUID(SERVICE_UUID).bytes_le)
            sockaddr.port = 0  # Windows resolves the Sony service through SDP.
            if ws.connect(handle, C.byref(sockaddr), C.sizeof(sockaddr)) != 0:
                error = ws.WSAGetLastError()
                if error not in (10035, 10036):
                    raise OSError(error, "RFCOMM connection failed; pair PCM-A10 under REC Remote first")
                deadline = time.monotonic() + timeout
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise ConnectionAbortedError("蓝牙连接已取消")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("Bluetooth connection timed out. Check power, pairing, and phone disconnection.")
                    _, writable, failed = select.select([], [stream], [stream], min(0.1, remaining) if cancel_event is not None else remaining)
                    if writable or failed:
                        break
                error = stream.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if error:
                    if error == 10049:
                        raise OSError(error, "Sony 控制服务暂无可用通道。A10 可能已有遥控连接；请断开其他遥控客户端，并确认使用 REC Remote 模式。")
                    if error == 10060:
                        raise OSError(error, "蓝牙连接暂时超时，请稍候重试；请确认录音机已开机并处于 REC Remote 模式。已有配对无需删除。")
                    raise OSError(error, "Bluetooth connection failed; check pairing and REC Remote mode")
            if cancel_event is not None and cancel_event.is_set():
                raise ConnectionAbortedError("蓝牙连接已取消")
            stream.settimeout(0.2)
        except BaseException:
            stream.close()
            raise
        self.stream = stream

    def recv(self, size: int = 4096) -> bytes:
        return self.stream.recv(size)

    def sendall(self, data: bytes):
        self.stream.sendall(data)

    def close(self):
        try:
            self.stream.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.stream.close()
