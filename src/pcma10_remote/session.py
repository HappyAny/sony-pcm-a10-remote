"""Serialized commands, a receive loop, and device-confirmed recorder state."""
from collections import deque
from dataclasses import dataclass, asdict
import threading
import time
from typing import Callable

from .protocol import (
    ACK, DATA, SHOT, Frame, FrameParser, ProtocolError, ack_for, bcd, encode,
    key, parse_capabilities, parse_hello, parse_items, set_gain, track_mark,
    get_item, set_item, parse_mark, parse_filename, meter_level,
)
from .settings import BY_KEY, BY_ITEM, catalog, choices_for, features

STATE_NAMES = {1: "已停止", 2: "暂停／录音待机", 3: "正在录音"}
BATTERY_NAMES = {1: "1 格", 2: "2 格", 3: "3 格", 4: "4 格", 5: "满电",
                 6: "充电中", 7: "电量低", 8: "温度限制", 9: "电量低"}


class SessionError(RuntimeError):
    pass


class CommandUncertainError(SessionError):
    """The frame may have arrived. Do not automatically repeat the action."""


@dataclass
class RecorderState:
    model: str = ""
    protocol: int = 0
    model_type: int = 0
    remote_status: int | None = None
    battery: int | None = None
    gain: int | None = None
    elapsed: str = "--:--:--"
    remaining: str = "--:--:--"
    left_db: float | None = None
    right_db: float | None = None
    left_over: bool = False
    right_over: bool = False
    rehearsal_status: int | None = None
    input_status: int | None = None
    scene_edited: bool | None = None
    sleep_timer: int | None = None
    pre_record_seconds: int | None = None
    filename: str = ""
    last_mark: dict | None = None
    peak_left_db: float | None = None
    peak_right_db: float | None = None
    peak_left_over: bool = False
    peak_right_over: bool = False


class RecorderSession:
    def __init__(self, transport, on_event: Callable[[dict], None] | None = None,
                 ack_timeout: float = 1.5):
        self.transport = transport
        self.on_event = on_event or (lambda event: None)
        self.ack_timeout = ack_timeout
        self.state = RecorderState()
        self.capabilities: dict[int, dict] = {}
        self._values: dict[int, int] = {}
        self._setting_errors: dict[str, str] = {}
        self.ready = False
        self._condition = threading.Condition()
        self._command_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._stop = threading.Event()
        self._hello = False
        self._error: str | None = None
        self._sequence = 0
        self._received_sequence: int | None = None
        self._pending_ack: int | None = None
        self._acknowledged = False
        self._serial = 0
        self._messages: deque[tuple[int, bytes]] = deque(maxlen=128)
        self._reader: threading.Thread | None = None
        self._operation_lock = threading.RLock()

    def emit(self, event: str, **data):
        try:
            self.on_event({"event": event, "time": time.time(), **data})
        except Exception:
            # UI/logging failures must not stop receipt of protocol ACKs.
            pass

    def snapshot(self) -> dict:
        with self._condition:
            return {**asdict(self.state), "ready": self.ready,
                    "settings": {setting.key: self._values.get(setting.item) for setting in BY_KEY.values()
                                 if choices_for(setting, self.capabilities)},
                    "features": features(self.capabilities),
                    "status_text": STATE_NAMES.get(self.state.remote_status, "未知")}

    def settings_snapshot(self):
        with self._condition:
            handled = set(BY_ITEM) | {0x0605}
            return {"state": self.snapshot(), "settings": catalog(self.capabilities, self._values),
                    "errors": self._setting_errors.copy(),
                    "additional_capabilities": [{"item": f"{item:04x}", **cap} for item, cap in self.capabilities.items()
                                                if item not in handled]}

    def _read_setting(self, item):
        result, values = parse_items(self._request(get_item(item), 0x18))
        if result not in (0, 0xA0) or item not in values:
            raise SessionError(f"设备未返回设置 {item:04X}（0x{result:02X}）")
        return values[item]

    def refresh_settings(self):
        with self._operation_lock:
            self._require_ready()
            self._setting_errors.clear()
            for setting in BY_KEY.values():
                if setting.kind == "action" or not choices_for(setting, self.capabilities):
                    continue
                try:
                    self._read_setting(setting.item)
                except SessionError as error:
                    self._check()  # A failed transport stops the whole refresh.
                    self._setting_errors[setting.key] = str(error)
            return self.settings_snapshot()

    def _write_setting(self, setting, value):
        result, returned = parse_items(self._request(set_item(setting.item, value), 0x16))
        if result not in (0, 0xA0):
            raise SessionError(f"录音机拒绝设置「{setting.title}」（0x{result:02X}）；请检查录音状态和相关输入设置")
        if setting.kind == "action":
            if returned.get(setting.item) != value:
                raise SessionError("设备未确认保存到所选 My Scene，未报告保存成功")
        elif self._read_setting(setting.item) != value:
            raise SessionError(f"设备读回的「{setting.title}」与请求不同，未报告设置成功")

    def set_setting(self, key_name, value):
        with self._operation_lock:
            self._require_ready()
            setting = BY_KEY.get(key_name)
            if setting is None:
                raise ValueError("未知录音设置；不允许写入未分析的能力项")
            choices = choices_for(setting, self.capabilities)
            if type(value) is not int or value not in choices:
                raise ValueError(f"当前录音机不支持「{setting.title}」的该选项")
            self._read_status()
            if self.state.rehearsal_status == 2:
                raise SessionError("请先结束试音，再更改录音设置")
            if setting.key == "auto-mark-interval":
                self._write_setting(BY_KEY["auto-mark"], 1)
            self._write_setting(setting, value)
            self.emit("setting_confirmed", key=setting.key, title=setting.title, value=value, label=choices[value])
            # Scene and input changes can change several related values.
            self.refresh_settings()
            return self.settings_snapshot()

    def _write(self, data: bytes):
        with self._write_lock:
            self.transport.sendall(data)
        self.emit("tx", hex=data.hex(" "))

    def _check(self):
        if self._error:
            raise SessionError(self._error)
        if self._stop.is_set():
            raise SessionError("连接已关闭")

    def _wait(self, predicate, timeout: float):
        deadline = time.monotonic() + timeout
        with self._condition:
            while not predicate():
                self._check()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("等待录音机响应超时")
                self._condition.wait(remaining)

    def connect(self, timeout: float = 8.0):
        if self._reader is not None:
            raise SessionError("This session has already been started")
        self.emit("connection", phase="等待录音机握手")
        self._reader = threading.Thread(target=self._read_loop, name="pcm-a10-receiver", daemon=True)
        self._reader.start()
        try:
            self._wait(lambda: self._hello, timeout)
            # No recorder operation is sent before a valid PCM-A10 greeting.
            self._send(b"\x01\x01")  # Application foreground
            response = self._request(b"\x02", 0x03)
            self.capabilities = parse_capabilities(response)
            self.refresh()
            with self._condition:
                self.ready = True
            self.emit("connected", model=self.state.model, protocol=f"0x{self.state.protocol:04x}")
            self.emit("state", state=self.snapshot())
            return self.snapshot()
        except BaseException:
            self.close()
            raise

    def close(self):
        with self._condition:
            was_open = not self._stop.is_set()
            self.ready = False
            self._stop.set()
            self._condition.notify_all()
        self.transport.close()
        if self._reader and threading.current_thread() is not self._reader:
            self._reader.join(timeout=1.0)
        if was_open:
            self.emit("disconnected")

    def _send(self, payload: bytes):
        # One reliable frame at a time. Actions are never automatically retried:
        # a lost ACK cannot establish whether recording or a mark was applied.
        with self._command_lock:
            with self._condition:
                self._check()
                sequence = self._sequence
                self._pending_ack = 1 - sequence
                self._acknowledged = False
            try:
                self._write(encode(Frame(DATA, sequence, payload)))
                self._wait(lambda: self._acknowledged, self.ack_timeout)
            except (TimeoutError, OSError, SessionError) as exc:
                self._error = "命令的送达结果未确认；请查看录音机，再重新连接。命令没有自动重发。"
                self.close()
                raise CommandUncertainError(self._error) from exc
            finally:
                with self._condition:
                    self._pending_ack = None
            self._sequence = 1 - sequence

    def _request(self, payload: bytes, expected_command: int, timeout: float = 3.0) -> bytes:
        # Callers serialize entire operations, including response matching.
        with self._condition:
            serial = self._serial
        self._send(payload)

        def found():
            return any(n > serial and p and p[0] == expected_command for n, p in self._messages)

        self._wait(found, timeout)
        with self._condition:
            return next(p for n, p in self._messages if n > serial and p and p[0] == expected_command)

    def refresh(self):
        with self._operation_lock:
            self._read_status()
            self._send(b"\x23")  # battery/display information
            self._send(b"\x17\x06\x04")  # recording gain
            self._send(b"\x30\x07\x06")  # elapsed time
            self._send(b"\x30\x07\x07")  # remaining recording time
            if self.state.remote_status in (2, 3) and not self.state.filename:
                self._request(b"\x33\x07\x08\x09", 0x34)
            return self.snapshot()

    def _read_status(self):
        response = self._request(b"\x20", 0x21)
        result, items = parse_items(response)
        if result or items.get(0x0704) not in STATE_NAMES:
            raise SessionError(f"未取得有效的录音机状态（返回码 0x{result:02x}）")

    def _read_gain(self) -> int:
        response = self._request(b"\x17\x06\x04", 0x18)
        result, items = parse_items(response)
        if result or 0x0604 not in items:
            raise SessionError(f"设备未确认录音增益：0x{result:02x}")
        return items[0x0604]

    def _require_ready(self):
        self._check()
        if not self.ready:
            raise SessionError("录音机握手尚未完成")

    def _confirm_status(self, expected: int):
        deadline = time.monotonic() + 2.5
        while time.monotonic() < deadline:
            if self.state.remote_status == expected:
                return self.snapshot()
            self._read_status()
            if self.state.remote_status == expected:
                return self.snapshot()
            with self._condition:
                self._condition.wait(0.15)
        raise SessionError(f"命令已获传输应答，但设备未确认「{STATE_NAMES[expected]}」；请查看录音机状态")

    def action(self, name: str, *, mark_name: str = "Mark", mark_type: int = 1, value: int | None = None):
        with self._operation_lock:
            self._require_ready()
            # Request fresh state before deciding whether an action is needed.
            self._read_status()
            if name == "record-toggle":
                name = "pause" if self.state.remote_status == 3 else "record"
            if name == "manual-input":
                setting, manual = {4: ("internal-mic", 8), 1: ("external-mic", 8), 2: ("audio-input", 4)}.get(self.state.input_status, (None, None))
                if setting is None:
                    raise SessionError("当前输入源未确认，请从录音设置页选择手动电平")
                self.set_setting(setting, manual)
                return self.snapshot()
            if name in ("rehearsal-start", "rehearsal-stop"):
                if not features(self.capabilities)["rehearsal"]:
                    raise SessionError("此录音机未报告试音功能")
                start = name == "rehearsal-start"
                active = self.state.rehearsal_status == 2
                if start == active:
                    return self.snapshot()
                if start and self.state.remote_status == 3:
                    raise SessionError("请先停止或暂停录音，再开始试音")
                if start and self.state.rehearsal_status == 3:
                    raise SessionError("试音需要手动录音电平；请点击“当前输入切为手动”，再开始试音")
                self._send(key(6 if start else 7))
                deadline = time.monotonic() + 3
                while True:
                    self._read_status()
                    if (self.state.rehearsal_status == 2) == start:
                        break
                    if time.monotonic() >= deadline:
                        raise SessionError("设备未确认试音状态，请查看录音机屏幕")
                    time.sleep(0.1)
                self.emit("action_confirmed", action=name)
                return self.snapshot()
            if name == "peak-reset":
                if not features(self.capabilities)["remote_peak_reset"]:
                    raise SessionError("此录音机未报告设备峰值清除功能")
                self._send(key(8))
                response = self._request(b"\x35\x07\x0d", 0x36)
                if response[1]:
                    raise SessionError("设备没有返回峰值信息")
                self.emit("action_requested", action=name)
                return self.snapshot()
            if name == "last-mark":
                self._request(b"\x13", 0x14)
                return self.snapshot()
            if name in ("record", "pause", "stop"):
                expected, code = {"record": (3, 3), "pause": (2, 2), "stop": (1, 1)}[name]
                if self.state.remote_status == expected:
                    return self.snapshot()
                self._send(key(code))
                self._confirm_status(expected)
            elif name in ("gain-up", "gain-down", "gain"):
                if self.state.remote_status not in (2, 3):
                    raise SessionError("请先进入录音或暂停／录音待机，再调整增益")
                if self.state.rehearsal_status == 2:
                    raise SessionError("试音期间由录音机自动调整增益，请先结束试音")
                previous = self._read_gain()
                if name == "gain":
                    if value is None:
                        raise ValueError("gain requires a value")
                    payload, expected = set_gain(value), value
                else:
                    payload = key(4 if name == "gain-up" else 5)
                    expected = max(0, min(30, previous + (1 if name == "gain-up" else -1)))
                    if previous == expected:
                        return self.snapshot()
                # A transport ACK or a successful later query does not override
                # the device's set-result. A real A10 returned 16 A1 ... here
                # while an unchanged gain query still returned success.
                result, _ = parse_items(self._request(payload, 0x16))
                if result:
                    hint = "；请检查手动录音电平／输入灵敏度设置" if result == 0xA1 else ""
                    raise SessionError(f"录音机拒绝调整增益（0x{result:02X}）{hint}")
                actual = self._read_gain()
                if actual != expected:
                    raise SessionError(f"设备返回的增益为 {actual}，未达到请求值 {expected}；请检查当前输入模式")
            elif name == "mark":
                if self.state.remote_status not in (2, 3):
                    raise SessionError("请先进入录音或暂停／录音待机，再添加标记")
                result = self._request(track_mark(mark_name, mark_type), 0x12)
                if parse_mark(result) is None:
                    raise SessionError("录音机未确认添加标记")
                if self.state.last_mark["type"] != mark_type or self.state.last_mark["name"] != mark_name:
                    raise SessionError("设备返回的标记与请求不同，未报告添加成功")
            elif name == "status":
                return self.refresh()
            else:
                raise ValueError(f"Unknown action: {name}")
            self.emit("action_confirmed", action=name)
            return self.snapshot()

    def _read_loop(self):
        parser = FrameParser()
        try:
            while not self._stop.is_set():
                try:
                    chunk = self.transport.recv(4096)
                except TimeoutError:
                    continue
                if not chunk:
                    raise ConnectionError("录音机关闭了蓝牙连接")
                self.emit("rx", hex=chunk.hex(" "))
                for frame in parser.feed(chunk):
                    if frame.kind == ACK:
                        with self._condition:
                            if frame.sequence == self._pending_ack:
                                self._acknowledged = True
                                self._condition.notify_all()
                    else:
                        if frame.kind == DATA:
                            self._write(encode(ack_for(frame)))
                            if self._received_sequence == frame.sequence:
                                self.emit("duplicate", sequence=frame.sequence)
                                continue
                            self._received_sequence = frame.sequence
                        try:
                            self._apply(frame.payload)
                        except ProtocolError as exc:
                            # Malformed greeting/capabilities invalidate handshake.
                            if frame.payload and frame.payload[0] in (0, 3):
                                raise
                            self.emit("protocol_error", message=str(exc))
                            continue
                        with self._condition:
                            self._serial += 1
                            self._messages.append((self._serial, frame.payload))
                            self._condition.notify_all()
                for error in parser.errors:
                    self.emit("protocol_error", message=error)
                parser.errors.clear()
        except Exception as exc:
            if not self._stop.is_set():
                with self._condition:
                    self._error = str(exc)
                    self.ready = False
                    self._stop.set()
                    self._condition.notify_all()
                self.transport.close()
                self.emit("error", message=str(exc))
                self.emit("disconnected")

    def _apply(self, payload: bytes):
        if not payload:
            raise ProtocolError("Empty command payload")
        command = payload[0]
        with self._condition:
            if command == 0:
                hello = parse_hello(payload)
                if hello["model"] != "PCM-A10":
                    raise ProtocolError(f"此版本只支持 PCM-A10，设备返回 {hello['model']!r}")
                if not 0x3000 <= hello["protocol"] <= 0x3010 or hello["model_type"] != 1:
                    raise ProtocolError("设备协议／型号类别不在本版本已分析的范围内")
                self.state.model = hello["model"]
                self.state.protocol = hello["protocol"]
                self.state.model_type = hello["model_type"]
                self._hello = True
            elif command == 3:
                parse_capabilities(payload)
            elif command in (0x16, 0x18, 0x21, 0x22, 0x24, 0x25):
                result, items = parse_items(payload)
                if result in (0, 0xA0):
                    self._values.update(items)
                    if 0x0704 in items:
                        self.state.remote_status = items[0x0704]
                        if self.state.remote_status == 1:
                            self.state.left_db = self.state.right_db = None
                            self.state.left_over = self.state.right_over = False
                            self.state.filename = ""
                    if 0x070B in items:
                        self.state.input_status = items[0x070B]
                    if 0x070E in items:
                        self.state.rehearsal_status = items[0x070E]
                    if 0x0703 in items:
                        self.state.scene_edited = items[0x0703] == 2
                    if 0x070A in items:
                        self.state.sleep_timer = items[0x070A]
                    if 0x070C in items:
                        self.state.pre_record_seconds = None if items[0x070C] == 0xFFFF else items[0x070C]
                    if 0x0701 in items:
                        self.state.battery = items[0x0701]
                    if 0x0604 in items:
                        self.state.gain = items[0x0604]
                else:
                    self.emit("device_result", command=f"0x{command:02x}", status=f"0x{result:02x}")
            elif command in (0x12, 0x14):
                self.state.last_mark = parse_mark(payload)
            elif command == 0x34:
                name = parse_filename(payload)
                if name is not None:
                    self.state.filename = name
            elif command == 0x36:
                if len(payload) < 3:
                    raise ProtocolError("Truncated peak response")
                if payload[1] == 0 and payload[2]:
                    if len(payload) != 11 or payload[2] != 1 or payload[3:5] != b"\x07\x0d" or any(value not in (0, 1) for value in payload[9:11]):
                        raise ProtocolError("Invalid peak response")
                    self.state.peak_left_db, self.state.peak_right_db = meter_level(*payload[5:7]), meter_level(*payload[7:9])
                    self.state.peak_left_over, self.state.peak_right_over = map(bool, payload[9:11])
            elif command == 0x41:
                if len(payload) != 6:
                    raise ProtocolError("Invalid recorder notification")
                message_id = int.from_bytes(payload[2:6], "big")
                self.emit("recorder_notice", status=payload[1], message_id=message_id,
                          message=f"录音机发来设备提示（{message_id}），请查看录音机屏幕。")
            elif command in (0x31, 0x32) and len(payload) >= 9:
                if payload[1] == 0 and payload[2] and payload[3] == 7:
                    hours = bcd(payload[5] & 15) * 100 + bcd(payload[6])
                    minutes, seconds = bcd(payload[7]), bcd(payload[8])
                    if minutes >= 60 or seconds >= 60:
                        raise ProtocolError("Invalid recorder time")
                    text = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
                    if payload[4] == 6:
                        self.state.elapsed = text
                    elif payload[4] == 7:
                        self.state.remaining = text
            elif command == 0x40:
                self._meter(payload)
            self._condition.notify_all()
        self.emit("state", state=self.snapshot())
        self.emit("payload", command=f"0x{command:02x}", hex=payload.hex(" "))

    def _meter(self, payload: bytes):
        if len(payload) < 2 or not payload[1]:
            return
        count = payload[1]
        peaks = None
        if len(payload) == 2 + 11 * count:
            record = payload[-11:]
            if record[2] > 99 or record[4] > 99:
                raise ProtocolError("Invalid meter fraction")
            left = -(record[1] * 100 + record[2]) / 100.0
            right = -(record[3] * 100 + record[4]) / 100.0
        elif len(payload) == 2 + 17 * count:
            record = payload[-17:]

            left, right = meter_level(record[1], record[2]), meter_level(record[3], record[4])
            peaks = (meter_level(record[11], record[12]), meter_level(record[13], record[14]))
            if record[15] not in (0, 1) or record[16] not in (0, 1):
                raise ProtocolError("Invalid peak overload flag")
        else:
            raise ProtocolError("Unrecognized meter record length")
        if record[0] not in (1, 2) or record[5] not in (0, 1) or record[6] not in (0, 1):
            raise ProtocolError("Invalid meter record")
        if peaks is not None:
            self.state.peak_left_db, self.state.peak_right_db = peaks
            self.state.peak_left_over, self.state.peak_right_over = bool(record[15]), bool(record[16])
        self.state.left_db, self.state.right_db = left, right
        self.state.left_over, self.state.right_over = bool(record[5]), bool(record[6])
