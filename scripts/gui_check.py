"""Run the native app's integration check against its internal simulator."""
from pathlib import Path
import argparse
import json
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--app-dir', type=Path)
parser.add_argument('--exe', type=Path)
args = parser.parse_args()
executable = args.exe.resolve() if args.exe else (args.app_dir.resolve() / 'PCM-A10-Fluent.exe' if args.app_dir else
    root / ('dist/standalone/PCM-A10.exe' if (root / 'dist/standalone/PCM-A10.exe').is_file() else 'dist/fluent/PCM-A10-Fluent.exe'))
if not executable.is_file():
    raise SystemExit('请先运行 python scripts/build_fluent.py')
report_path = executable.parent / 'self-test.json'
report_path.unlink(missing_ok=True)
error_path = executable.parent / 'self-test-error.txt'
error_path.unlink(missing_ok=True)
completed = subprocess.run([str(executable), '--self-test', '--exit-after-self-test', '--capture-preview'],
                           cwd=executable.parent, timeout=60, capture_output=True, text=True, encoding='utf-8', errors='replace')
if not report_path.is_file():
    raise SystemExit('Fluent 自检未生成结果')
report = json.loads(report_path.read_text(encoding='utf-8'))
report['processExitCode'] = completed.returncode
report['success'] = report['success'] and completed.returncode == 0
if completed.returncode:
    report['processError'] = error_path.read_text(encoding='utf-8') if error_path.exists() else completed.stderr
report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(json.dumps(report, ensure_ascii=False, indent=2))
if completed.returncode or not report['success']:
    sys.exit(1)
