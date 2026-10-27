"""Private JSON-lines transport between the native Fluent UI and recorder core.

The child process belongs to one UI window. It does not open a network port.
"""
from dataclasses import asdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import threading

from . import __version__
from .session import RecorderSession


class BridgeHost:
    def __init__(self, output, *, demo=False, transport_factory=None, discovery=None):
        self.output = output
        self.demo = demo
        self.transport_factory = transport_factory
        self.discovery = discovery
        self.session = None
        self.trace_file = None
        self.lock = threading.RLock()
        self.lifecycle_lock = threading.Lock()
        self.closed = False
        self.generation = 0
        self.connection_cancel = threading.Event()

    def send(self, message):
        with self.lock:
            self.output.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
            self.output.flush()

    def event(self, event):
        with self.lock:
            if self.trace_file:
                try:
                    self.trace_file.write(json.dumps(event, ensure_ascii=False) + "\n")
                    self.trace_file.flush()
                except OSError:
                    self.trace_file.close()
                    self.trace_file = None
                    self.send({"type": "event", "data": {"event": "error", "message": "通信日志写入失败"}})
            if event["event"] not in ("tx", "rx", "payload", "duplicate"):
                self.send({"type": "event", "data": event})

    def dispatch(self, method, params):
        if self.closed and method != "shutdown":
            raise RuntimeError("控制服务已关闭")
        if method == "devices":
            if self.demo:
                return [{"name": "PCM-A10 · 模拟设备", "address": "demo", "paired": True, "connected": bool(self.session and self.session.ready)}]
            if self.discovery:
                devices = self.discovery(inquiry=bool(params.get("scan", False)))
            else:
                from .bluetooth_windows import discover
                devices = discover(inquiry=bool(params.get("scan", False)))
            return [asdict(device) for device in devices]
        if method == "connect":
            with self.lifecycle_lock:
                if self.closed:
                    raise RuntimeError("控制服务已关闭")
                if self.session and self.session.ready:
                    return self.session.snapshot()
                previous, self.session = self.session, None
                generation = self.generation
                cancel = self.connection_cancel = threading.Event()
            if previous:
                previous.close()
            for attempt in range(2):
                try:
                    if self.transport_factory:
                        transport = self.transport_factory(params.get("address", ""))
                    elif self.demo:
                        from .simulator import SimulatedTransport
                        transport = SimulatedTransport()
                    else:
                        from .bluetooth_windows import WindowsRfcomm
                        transport = WindowsRfcomm(str(params.get("address", "")), cancel_event=cancel)
                    break
                except OSError as error:
                    transient = isinstance(error, TimeoutError) or error.errno in (10049, 10060)
                    if attempt or not transient or cancel.is_set():
                        raise
                    self.send({"type": "event", "data": {"event": "connection", "phase": "正在等待录音机重新开放连接…"}})
                    # Retry only RFCOMM establishment, before any recorder command.
                    if cancel.wait(1.2):
                        raise ConnectionAbortedError("蓝牙连接已取消") from error
            session = RecorderSession(transport, self.event)
            with self.lifecycle_lock:
                if self.closed or generation != self.generation or cancel.is_set():
                    transport.close()
                    raise RuntimeError("蓝牙连接已取消")
                self.session = session
            return session.connect()
        if method == "disconnect":
            state = self.disconnect()
            return {"ready": False, "last_state": state}
        if method == "trace":
            with self.lock:
                if self.trace_file:
                    self.trace_file.close()
                    self.trace_file = None
                if not params.get("enabled", False):
                    return {"enabled": False}
                folder = Path.cwd() / "logs"
                folder.mkdir(exist_ok=True)
                path = folder / datetime.now().strftime("fluent-%Y%m%d-%H%M%S.jsonl")
                self.trace_file = path.open("a", encoding="utf-8")
                return {"enabled": True, "path": str(path)}
        if method == "shutdown":
            state = self.close()
            return {"closed": True, "last_state": state}
        session = self.session
        if not session or not session.ready:
            raise RuntimeError("请先连接录音机")
        if method == "refresh":
            return session.refresh()
        if method == "settings":
            return session.refresh_settings()
        if method == "settings-set":
            return session.set_setting(str(params.get("key", "")), params.get("value"))
        if method == "action":
            return session.action(str(params.get("name", "")),
                                       mark_name=str(params.get("mark_name", "Mark")),
                                       mark_type=params.get("mark_type", 1),
                                       value=params.get("value"))
        raise ValueError(f"Unknown bridge method: {method}")

    def handle(self, request):
        request_id = request.get("id")
        try:
            method = str(request.get("method", ""))
            params = request.get("params") or {}
            if not isinstance(params, dict):
                raise ValueError("params must be an object")
            result = self.dispatch(method, params)
            self.send({"type": "response", "id": request_id, "ok": True, "result": result})
        except Exception as exc:
            self.send({"type": "response", "id": request_id, "ok": False,
                       "error": {"message": str(exc), "kind": type(exc).__name__}})

    def disconnect(self, *, shutdown=False):
        with self.lifecycle_lock:
            if shutdown:
                self.closed = True
            self.generation += 1
            self.connection_cancel.set()
            session, self.session = self.session, None
        if session:
            session.close()
            return session.snapshot()
        return None

    def close(self):
        state = self.disconnect(shutdown=True)
        with self.lock:
            if self.trace_file:
                self.trace_file.close()
                self.trace_file = None
        return state


def run(*, demo=False):
    # The desktop frontend always redirects these streams; explicitly use UTF-8
    # instead of inheriting the Windows console code page.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    host = BridgeHost(sys.stdout, demo=demo)
    host.send({"type": "ready", "version": __version__, "demo": demo, "pid": os.getpid()})
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="recorder-command")
    pending = set()
    try:
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("Request must be a JSON object")
            except (ValueError, TypeError) as exc:
                host.send({"type": "response", "id": None, "ok": False,
                           "error": {"kind": "InvalidRequest", "message": str(exc)}})
                continue
            method = request.get("method")
            if method in ("shutdown", "disconnect"):
                # Disconnect must interrupt a blocked connection/command instead
                # of waiting behind it on the serialized command worker.
                for future in pending:
                    future.cancel()
                pending.clear()
                host.handle(request)
                if method == "shutdown":
                    break
            else:
                pending = {future for future in pending if not future.done()}
                pending.add(executor.submit(host.handle, request))
    finally:
        host.close()
        executor.shutdown(wait=True, cancel_futures=True)


if __name__ == "__main__":
    run(demo="--demo" in sys.argv)
