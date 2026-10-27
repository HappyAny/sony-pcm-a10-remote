import argparse
from dataclasses import asdict
import json
import sys

from .session import RecorderSession
from .settings import BY_KEY


def main(argv=None):
    parser = argparse.ArgumentParser(description="Sony PCM-A10 非官方蓝牙遥控（Windows）")
    parser.add_argument("--demo", action="store_true", help="使用本地模拟录音机，不访问蓝牙")
    subs = parser.add_subparsers(dest="command")
    subs.add_parser("gui", help="打开桌面界面（默认）")
    subs.add_parser("doctor", help="检查 Windows 蓝牙接口，不连接录音机")
    scan = subs.add_parser("devices", help="列出 PCM-A10")
    scan.add_argument("--scan", action="store_true", help="执行约 8 秒的设备搜索")
    scan.add_argument("--all", action="store_true", help="列出所有蓝牙设备，供设备被重命名时诊断")
    command = subs.add_parser("command", help="连接后执行一次命令，再断开")
    command.add_argument("action", choices=["status", "record", "record-toggle", "pause", "stop", "mark", "gain-up", "gain-down", "gain", "rehearsal-start", "rehearsal-stop", "manual-input", "last-mark", "peak-reset"])
    command.add_argument("--address", help="已配对的 PCM-A10 蓝牙地址")
    command.add_argument("--name", default="Mark", help="标记名称")
    command.add_argument("--type", type=int, choices=[1, 2, 3, 4], default=1, help="标记类别：普通、重要、问题、新话题")
    command.add_argument("--value", type=int, help="录音增益 0..30")
    command.add_argument("--trace", action="store_true", help="向标准错误输出协议日志")
    subs.add_parser("demo-check", help="运行模拟器控制流程，输出验证结果")
    settings = subs.add_parser("settings", help="读取全部受支持的录音设置及选项")
    settings.add_argument("--address", help="已配对的 PCM-A10 蓝牙地址")
    setting = subs.add_parser("set-setting", help="更改一项设置，并读取设备确认值")
    setting.add_argument("key", choices=list(BY_KEY))
    setting.add_argument("value", type=int)
    setting.add_argument("--address", help="已配对的 PCM-A10 蓝牙地址")
    args = parser.parse_args(argv)
    try:
        if args.command in (None, "gui"):
            from .native_gui import run
            run(demo=args.demo)
            return 0
        if args.command == "doctor":
            from .bluetooth_windows import doctor
            print(json.dumps(doctor(), ensure_ascii=False, indent=2))
            return 0
        if args.command == "devices":
            from .bluetooth_windows import discover
            print(json.dumps([asdict(d) for d in discover(inquiry=args.scan, all_devices=args.all)], ensure_ascii=False, indent=2))
            return 0
        if args.demo or args.command == "demo-check":
            from .simulator import SimulatedTransport
            transport = SimulatedTransport()
        else:
            if not args.address:
                parser.error("真实蓝牙命令需要 --address；先执行 devices 或在界面中连接")
            from .bluetooth_windows import WindowsRfcomm
            transport = WindowsRfcomm(args.address)
        callback = None
        if getattr(args, "trace", False):
            callback = lambda event: print(json.dumps(event, ensure_ascii=False), file=sys.stderr)
        session = RecorderSession(transport, callback)
        try:
            session.connect()
            if args.command == "demo-check":
                results = []
                for action in ("record", "gain-up", "mark", "pause", "record", "stop"):
                    state = session.action(action)
                    results.append({"action": action, "status": state["remote_status"], "gain": state["gain"]})
                print(json.dumps({"mode": "simulation", "results": results, "marks": transport.marks,
                                  "hardware_control_verified": False}, ensure_ascii=False, indent=2))
            elif args.command in ("settings", "set-setting"):
                result = session.refresh_settings() if args.command == "settings" else session.set_setting(args.key, args.value)
                print(json.dumps({"mode": "simulation" if args.demo else "bluetooth", **result}, ensure_ascii=False, indent=2))
            else:
                state = session.action(args.action, mark_name=args.name, mark_type=args.type, value=args.value)
                print(json.dumps({"mode": "simulation" if args.demo else "bluetooth", "state": state}, ensure_ascii=False, indent=2))
        finally:
            session.close()
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
