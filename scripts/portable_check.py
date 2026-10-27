"""Verify that only the portable EXE is needed, including cleanup after a crash."""
from pathlib import Path
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--exe', type=Path, default=root / 'dist/standalone/PCM-A10.exe')
executable = parser.parse_args().exe.resolve()
build_root = (root / 'build').resolve()
report = {
    'created_at': datetime.now(ZoneInfo('Asia/Singapore')).isoformat(), 'success': False,
    'mode': 'simulation', 'hardware_commands_sent': False,
    'bytes': executable.stat().st_size, 'sha256': hashlib.sha256(executable.read_bytes()).hexdigest()
}
with tempfile.TemporaryDirectory(prefix='portable-check-', dir=build_root) as temporary:
    sandbox = Path(temporary).resolve()
    assert sandbox.is_relative_to(build_root)  # Cleanup is confined to this owned test directory.
    launch = sandbox / '独立目录 空格'
    launch.mkdir()
    target = launch / 'PCM-A10.exe'
    shutil.copy2(executable, target)
    report['only_exe_copied'] = [path.name for path in launch.iterdir()] == ['PCM-A10.exe']
    env = os.environ.copy()
    for key in ('PYTHONHOME', 'PYTHONPATH'):
        env.pop(key, None)
    env.update({
        'PATH': str(Path(os.environ['SystemRoot']) / 'System32'),
        'DOTNET_ROOT': str(sandbox / 'no-installed-dotnet'), 'DOTNET_ROOT_X64': str(sandbox / 'no-installed-dotnet'),
        'DOTNET_MULTILEVEL_LOOKUP': '0', 'DOTNET_BUNDLE_EXTRACT_BASE_DIR': str(sandbox / 'bundle-runtime'),
        'TEMP': str(sandbox / 'temp'), 'TMP': str(sandbox / 'temp'), 'PYTHONIOENCODING': 'utf-8'
    })
    Path(env['TEMP']).mkdir()
    def run_check(script):
        completed = subprocess.run([sys.executable, str(root / 'scripts' / script), '--exe', str(target)],
                                   cwd=sandbox, env=env, capture_output=True, text=True, encoding='utf-8', timeout=90)
        if completed.returncode:
            raise RuntimeError(f'{script} failed ({completed.returncode}):\n{completed.stdout}\n{completed.stderr}')
    try:
        assert report['only_exe_copied']
        run_check('gui_check.py')
        first = json.loads((launch / 'self-test.json').read_text(encoding='utf-8'))
        assert first['success'] and first['processExitCode'] == 0
        assert first['deployment']['embeddedBackend'] and first['deployment']['backendRuntimeRemoved']
        assert first['deployment']['runtimeVersion'] == '10.0.14' and first['licenseDocuments'] == 9
        report['native_self_test'] = first
        report['installed_dotnet_and_python_paths_removed'] = True
        run_check('lifecycle_check.py')
        crash = json.loads((build_root / 'lifecycle-check.json').read_text(encoding='utf-8'))
        report['abnormal_exit'] = crash
        stale = Path(crash['runtime_directory'])
        assert stale.is_relative_to(sandbox) and stale.is_dir()
        run_check('gui_check.py')  # A fresh run must clear the previous crashed window's private runtime.
        second = json.loads((launch / 'self-test.json').read_text(encoding='utf-8'))
        assert second['success'] and not stale.exists()
        report['crashed_runtime_removed_on_next_start'] = True
        report['normal_runtime_removed_on_exit'] = second['deployment']['backendRuntimeRemoved']
        for name in ('light', 'dark', 'small', 'settings', 'battery'):
            shutil.copy2(launch / f'preview-{name}.png', build_root / f'portable-preview-{name}.png')
        report['success'] = True
    except Exception as error:
        report['error'] = f'{type(error).__name__}: {error}'
path = build_root / 'portable-check.json'
path.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(json.dumps({key: value for key, value in report.items() if key not in ('native_self_test',)}, ensure_ascii=False, indent=2))
raise SystemExit(0 if report['success'] else 1)
