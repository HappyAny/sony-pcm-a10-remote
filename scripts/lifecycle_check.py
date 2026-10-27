"""Crash only an owned simulator UI and verify its backend exits with it."""
from pathlib import Path
import argparse
import ctypes as C
from ctypes import wintypes as W
import json
import subprocess
import time

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--app-dir', type=Path)
parser.add_argument('--exe', type=Path)
args = parser.parse_args()
executable = args.exe.resolve() if args.exe else (args.app_dir.resolve() / 'PCM-A10-Fluent.exe' if args.app_dir else
    root / ('dist/standalone/PCM-A10.exe' if (root / 'dist/standalone/PCM-A10.exe').is_file() else 'dist/fluent/PCM-A10-Fluent.exe'))
folder = executable.parent
probe = folder / 'lifetime-probe.json'
probe.unlink(missing_ok=True)
kernel = C.WinDLL('kernel32', use_last_error=True)
kernel.OpenProcess.argtypes, kernel.OpenProcess.restype = [W.DWORD, W.BOOL, W.DWORD], W.HANDLE
kernel.WaitForSingleObject.argtypes, kernel.WaitForSingleObject.restype = [W.HANDLE, W.DWORD], W.DWORD
kernel.TerminateProcess.argtypes, kernel.TerminateProcess.restype = [W.HANDLE, W.UINT], W.BOOL
kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = [W.HANDLE], W.BOOL
process = subprocess.Popen([str(executable), '--lifetime-probe'], cwd=folder)
handles = []
report = {'mode': 'simulation', 'hardware_commands_sent': False, 'success': False}
try:
    deadline = time.monotonic() + 18
    while not probe.exists():
        if process.poll() is not None or time.monotonic() >= deadline:
            raise RuntimeError('模拟生命周期探针未就绪')
        time.sleep(0.05)
    data = json.loads(probe.read_text(encoding='utf-8'))
    if data['frontendPid'] != process.pid or data['mode'] != 'simulation' or not data['guard']:
        raise RuntimeError('模拟探针身份或退出保护不匹配')
    for pid in set([data['backendPid'], data['workerPid']]):
        handle = kernel.OpenProcess(0x00100001, False, pid)  # synchronize + terminate, pinned before the test
        if not handle or kernel.WaitForSingleObject(handle, 0) != 258:
            raise RuntimeError('模拟后端在测试前已经退出')
        handles.append((pid, handle))
    process.kill()  # Only the UI created above. Do not kill its process tree.
    process.wait(timeout=5)
    exited = {str(pid): kernel.WaitForSingleObject(handle, 5000) == 0 for pid, handle in handles}
    report.update({'frontend_killed': True, 'job_guard': True, 'backend_exit_results': exited,
                   'recorder_status_before_crash': data['remoteStatus'], 'success': all(exited.values()),
                   'runtime_directory': data.get('runtimeDirectory')})
    if not report['success']:
        raise RuntimeError('模拟后端在主窗口异常退出后仍然运行')
except Exception as error:
    report['error'] = str(error)
finally:
    if process.poll() is None:
        process.kill()
        process.wait(timeout=5)
    for _, handle in handles:
        if kernel.WaitForSingleObject(handle, 0) == 258:
            # Cleanup is after the result is recorded and cannot make it pass.
            kernel.TerminateProcess(handle, 1)
        kernel.CloseHandle(handle)
path = root / 'build/lifecycle-check.json'
path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(report, ensure_ascii=False, indent=2))
raise SystemExit(0 if report['success'] else 1)
