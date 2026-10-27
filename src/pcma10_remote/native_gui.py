"""Launch the native Windows Fluent desktop application."""
from pathlib import Path
import os
import subprocess


def run(*, demo=False):
    if os.name != "nt":
        raise OSError("Fluent 桌面界面需要 Windows 10/11。")
    root = Path(__file__).resolve().parents[2]
    executable = next((path for path in (root / "dist/standalone/PCM-A10.exe", root / "dist/fluent/PCM-A10-Fluent.exe") if path.is_file()), None)
    if executable is None:
        raise RuntimeError("尚未构建桌面界面，请先运行 python scripts/build_portable.py；构建需要 .NET 10 SDK 和 PyInstaller。")
    subprocess.Popen([str(executable), *(["--demo"] if demo else [])], cwd=executable.parent)
