"""Small native desktop remote; all Bluetooth I/O runs off the Tk thread."""
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext

from .session import BATTERY_NAMES, RecorderSession


class RemoteWindow:
    def __init__(self, root: tk.Tk, *, demo=False):
        self.root, self.demo = root, demo
        self.session = None
        self.events = queue.Queue()
        self.tasks = queue.Queue()
        self.worker = threading.Thread(target=self._work, name="pcm-a10-commands", daemon=True)
        self.worker.start()
        self.devices = []
        self.busy = False
        self.closing = False
        self.trace_file = None
        self.root.title("PCM-A10 蓝牙遥控" + (" · 模拟模式" if demo else " · 待实机验证"))
        self.root.geometry("830x705")
        self.root.minsize(760, 660)
        self.root.option_add("*Font", ("Microsoft YaHei UI", 10))
        self.address = tk.StringVar()
        self.status = tk.StringVar(value="尚未连接")
        self.detail = tk.StringVar(value="等待录音机返回状态")
        self.elapsed = tk.StringVar(value="--:--:--")
        self.remaining = tk.StringVar(value="--:--:--")
        self.gain = tk.StringVar(value="--")
        self.battery = tk.StringVar(value="--")
        self.meter_l = tk.StringVar(value="-- dBFS")
        self.meter_r = tk.StringVar(value="-- dBFS")
        self.trace = tk.BooleanVar(value=False)
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(60, self._drain)
        self.root.after(2000, self._poll)
        if demo:
            self.connect()
        else:
            self.scan(False)

    def _build(self):
        frame = ttk.Frame(self.root, padding=18)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        banner = "模拟模式：所有状态和电平均由本地模拟器生成" if self.demo else "非官方实验版本 · 蓝牙控制尚待 PCM-A10 实机验证"
        tk.Label(frame, text=banner, foreground="#a04700", anchor="w").grid(row=0, sticky="ew", pady=(0, 12))
        connection = ttk.LabelFrame(frame, text="连接录音机", padding=10)
        connection.grid(row=1, sticky="ew")
        connection.columnconfigure(0, weight=1)
        self.combo = ttk.Combobox(connection, textvariable=self.address)
        self.combo.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.scan_button = ttk.Button(connection, text="搜索 PCM-A10", command=lambda: self.scan(True))
        self.scan_button.grid(row=0, column=1, padx=3)
        self.connect_button = ttk.Button(connection, text="连接", command=self.connect)
        self.connect_button.grid(row=0, column=2, padx=3)
        self.disconnect_button = ttk.Button(connection, text="断开", command=self.disconnect)
        self.disconnect_button.grid(row=0, column=3, padx=3)
        ttk.Button(connection, text="Windows 配对", command=self.pair_settings).grid(row=0, column=4, padx=3)
        ttk.Label(connection, text="首次：A10 → Bluetooth → REC Remote → Add Device；随后在 Windows 蓝牙设置中配对。",
                  wraplength=740).grid(row=1, column=0, columnspan=5, sticky="w", pady=(8, 0))
        panel = ttk.LabelFrame(frame, text="设备返回的状态", padding=12)
        panel.grid(row=2, sticky="ew", pady=12)
        tk.Label(panel, textvariable=self.status, font=("Microsoft YaHei UI", 21, "bold"), anchor="w").grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(panel, textvariable=self.detail).grid(row=1, column=0, columnspan=4, sticky="w", pady=(2, 12))
        for column, (title, variable) in enumerate([
            ("录音时间", self.elapsed), ("剩余时间", self.remaining),
            ("录音增益", self.gain), ("电池", self.battery),
        ]):
            panel.columnconfigure(column, weight=1)
            ttk.Label(panel, text=title).grid(row=2, column=column, sticky="w")
            ttk.Label(panel, textvariable=variable, font=("Consolas", 16)).grid(row=3, column=column, sticky="w", pady=(3, 8))
        for row, (title, variable) in enumerate([("L", self.meter_l), ("R", self.meter_r)], start=4):
            ttk.Label(panel, text=title).grid(row=row, column=0, sticky="w")
            bar = ttk.Progressbar(panel, maximum=60)
            bar.grid(row=row, column=1, columnspan=2, sticky="ew", padx=10, pady=3)
            ttk.Label(panel, textvariable=variable).grid(row=row, column=3, sticky="e")
            if title == "L":
                self.bar_l = bar
            else:
                self.bar_r = bar
        controls = ttk.Frame(frame)
        controls.grid(row=3, sticky="ew")
        self.action_buttons = []
        for column, (label, action) in enumerate([
            ("● 录音／继续", "record"), ("Ⅱ 暂停／待机", "pause"), ("■ 停止", "stop"),
            ("添加标记", "mark"), ("增益 −", "gain-down"), ("增益 +", "gain-up"),
        ]):
            controls.columnconfigure(column, weight=1)
            button = ttk.Button(controls, text=label, command=lambda name=action: self.run_action(name))
            button.grid(row=0, column=column, sticky="ew", padx=3, ipady=8)
            self.action_buttons.append(button)
        ttk.Label(frame, text="暂停可用于录音待机。关闭程序只会断开连接，录音是否继续以设备状态为准。",
                  foreground="#555555").grid(row=4, sticky="w", pady=(10, 8))
        log_head = ttk.Frame(frame)
        log_head.grid(row=5, sticky="ew")
        ttk.Label(log_head, text="操作记录").pack(side="left")
        ttk.Checkbutton(log_head, text="记录通信到本地日志", variable=self.trace, command=self.toggle_trace).pack(side="right")
        self.log_widget = scrolledtext.ScrolledText(frame, height=10, wrap="word", font=("Microsoft YaHei UI", 9), state="disabled")
        self.log_widget.grid(row=6, sticky="nsew", pady=(6, 0))
        frame.rowconfigure(6, weight=1)
        self._update_buttons()

    def _log(self, text):
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", datetime.now().strftime("%H:%M:%S  ") + text + "\n")
        if int(self.log_widget.index("end-1c").split(".")[0]) > 300:
            self.log_widget.delete("1.0", "50.0")
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")

    def _work(self):
        while True:
            task = self.tasks.get()
            if task is None:
                return
            try:
                task()
            except Exception as exc:
                self.events.put({"event": "error", "message": str(exc)})
            finally:
                self.events.put({"event": "task_done"})

    def _submit(self, task):
        if self.busy or self.closing:
            return
        self.busy = True
        self._update_buttons()
        self.tasks.put(task)

    def scan(self, inquiry):
        if self.demo:
            return

        def task():
            from .bluetooth_windows import discover
            self.events.put({"event": "devices", "devices": discover(inquiry=inquiry)})

        self._log("正在搜索…" if inquiry else "读取已配对的 PCM-A10…")
        self._submit(task)

    def connect(self):
        if self.busy or (self.session and self.session.ready):
            return
        selection = self.address.get().strip()
        address = selection.rsplit(" | ", 1)[-1]
        if not self.demo and not address:
            self._log("请先在 Windows 中配对并搜索，或输入设备的蓝牙地址。")
            return
        self.status.set("正在连接…")

        def task():
            if self.demo:
                from .simulator import SimulatedTransport
                transport = SimulatedTransport()
            else:
                from .bluetooth_windows import WindowsRfcomm
                transport = WindowsRfcomm(address)
            session = RecorderSession(transport, self.events.put)
            self.session = session
            session.connect()

        self._submit(task)

    def disconnect(self):
        if self.session:
            self.session.close()
            self.session = None
        self.status.set("已断开")
        self._update_buttons()

    def run_action(self, action):
        session = self.session
        if session:
            self._submit(lambda: session.action(action))

    def pair_settings(self):
        if os.name == "nt":
            os.startfile("ms-settings:bluetooth")

    def toggle_trace(self):
        if self.trace.get():
            try:
                base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd()
                folder = base / "logs"
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / (datetime.now().strftime("pcma10-%Y%m%d-%H%M%S") + ".jsonl")
                self.trace_file = path.open("a", encoding="utf-8")
                self._log(f"通信日志：{path}")
            except OSError as exc:
                self.trace.set(False)
                self._log(f"无法写入日志：{exc}")
        elif self.trace_file:
            self.trace_file.close()
            self.trace_file = None

    def _update_buttons(self):
        ready = bool(self.session and self.session.ready)
        for button in self.action_buttons:
            button.configure(state="normal" if ready and not self.busy else "disabled")
        self.connect_button.configure(state="normal" if not ready and not self.busy else "disabled")
        self.disconnect_button.configure(state="normal" if self.session else "disabled")
        self.scan_button.configure(state="normal" if not self.demo and not ready and not self.busy else "disabled")
        self.combo.configure(state="disabled" if ready or self.busy else "normal")

    def _state(self, state):
        self.status.set(state["status_text"] if state["ready"] else "握手／状态同步中…")
        self.detail.set(f"{state['model'] or 'PCM-A10'}  ·  协议 0x{state['protocol']:04X}" + ("  ·  模拟数据" if self.demo else ""))
        self.elapsed.set(state["elapsed"])
        self.remaining.set(state["remaining"])
        self.gain.set(str(state["gain"]) if state["gain"] is not None else "--")
        self.battery.set(BATTERY_NAMES.get(state["battery"], "--"))
        for channel, variable, bar in [("left", self.meter_l, self.bar_l), ("right", self.meter_r, self.bar_r)]:
            value = state[channel + "_db"]
            variable.set(f"{value:.1f} dBFS" + (" OVER" if state[channel + "_over"] else "") if value is not None else "-- dBFS")
            bar["value"] = max(0, min(60, value + 60)) if value is not None else 0

    def _drain(self):
        if self.closing:
            return
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event["event"]
            if self.trace_file:
                try:
                    safe = {k: v for k, v in event.items() if k != "devices"}
                    self.trace_file.write(json.dumps(safe, ensure_ascii=False) + "\n")
                    self.trace_file.flush()
                except OSError as exc:
                    self.trace.set(False)
                    self.toggle_trace()
                    self._log(f"日志写入失败：{exc}")
            if kind == "state":
                self._state(event["state"])
            elif kind == "devices":
                self.devices = event["devices"]
                choices = [f"{d.name} {'(已配对)' if d.paired else '(未配对)'} | {d.address}" for d in self.devices]
                self.combo["values"] = choices
                if choices and not self.address.get():
                    self.address.set(choices[0])
                self._log(f"发现 {len(choices)} 台 PCM-A10。" if choices else "未发现 PCM-A10。请进入 REC Remote 配对模式，并在 Windows 蓝牙设置中配对。")
            elif kind == "connection":
                self.status.set(event["phase"])
            elif kind == "connected":
                self._log("录音机握手与状态查询完成。")
            elif kind == "disconnected":
                self.status.set("已断开")
                self._log("蓝牙连接已断开。")
            elif kind == "action_confirmed":
                labels = {"record": "录音／继续", "pause": "暂停／待机", "stop": "停止", "mark": "添加标记", "gain-up": "提高增益", "gain-down": "降低增益"}
                self._log("设备已确认：" + labels.get(event["action"], event["action"]))
            elif kind in ("error", "protocol_error"):
                self._log(event["message"])
                if kind == "error" and not (self.session and self.session.ready):
                    self.status.set("连接未完成／已断开")
            elif kind == "device_result":
                self._log(f"录音机返回状态 {event['status']}（命令 {event['command']}）")
            elif kind == "task_done":
                self.busy = False
            self._update_buttons()
        self.root.after(60, self._drain)

    def _poll(self):
        if self.closing:
            return
        if self.session and self.session.ready and not self.busy:
            session = self.session
            self._submit(session.refresh)
        self.root.after(2000, self._poll)

    def close(self):
        self.closing = True
        if self.session:
            self.session.close()
        if self.trace_file:
            self.trace_file.close()
        self.tasks.put(None)
        self.root.destroy()


def run(demo=False):
    if not getattr(sys, "frozen", False):
        vendor = Path(__file__).resolve().parents[2] / "vendor"
        for variable, folder in (("TCL_LIBRARY", "tcl8.6"), ("TK_LIBRARY", "tk8.6")):
            if (vendor / folder).is_dir():
                # Process-local resources also work when the Python installation
                # lives outside the workspace's native-library read boundary.
                os.environ[variable] = str(vendor / folder)
    root = tk.Tk()
    RemoteWindow(root, demo=demo)
    root.mainloop()
