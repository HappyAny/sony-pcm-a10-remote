"""Read A10 settings; optionally round-trip pre-record and restore its value.

Never starts/stops recording, changes input/gain, or overwrites My Scene.
Run as an ordinary Windows process, outside a Bluetooth-restricted sandbox.
"""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import argparse
import json
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'src'))
from pcma10_remote.bluetooth_windows import discover, WindowsRfcomm
from pcma10_remote.session import RecorderSession

parser = argparse.ArgumentParser()
parser.add_argument('--write-test', action='store_true', help='停止状态下临时切换预录音，然后恢复原值')
args = parser.parse_args()
report = {'created_at': datetime.now(ZoneInfo('Asia/Singapore')).isoformat(),
          'transport': 'Windows RFCOMM; Python source core', 'read_passed': False,
          'write_test_requested': args.write_test, 'write_test_passed': False,
          'recording_commands_sent': False, 'preset_overwritten': False, 'steps': []}
session = None
original_pre = None
restore_needed = False
try:
    devices = [device for device in discover() if device.paired]
    if len(devices) != 1:
        raise RuntimeError(f'需要唯一的已配对 PCM-A10，当前数量：{len(devices)}')
    session = RecorderSession(WindowsRfcomm(devices[0].address))
    state = session.connect()
    result = session.refresh_settings()
    report.update({'initial_remote_status': state['remote_status'], 'initial_gain': state['gain'],
                   'initial_rehearsal_status': state['rehearsal_status'],
                   'settings': result['settings'], 'read_errors': result['errors'],
                   'additional_capabilities': result['additional_capabilities'],
                   'read_passed': not result['errors']})
    before = result['state']['settings']
    if args.write_test:
        if state['remote_status'] != 1 or state['rehearsal_status'] == 2:
            report['write_skip_reason'] = '设备正在录音、待机或试音，仅完成读取，不改变当前状态。'
        elif before.get('pre-record') in (1, 2):
            original_pre = before['pre-record']
            target = 3 - original_pre
            restore_needed = True
            changed = session.set_setting('pre-record', target)
            report['steps'].append({'key': 'pre-record', 'requested': target,
                                    'confirmed': changed['state']['settings']['pre-record']})
            restored = session.set_setting('pre-record', original_pre)
            restore_needed = False
            report['steps'].append({'key': 'pre-record', 'requested': original_pre,
                                    'confirmed': restored['state']['settings']['pre-record']})
            report['all_setting_values_restored'] = restored['state']['settings'] == before
            report['write_test_passed'] = report['all_setting_values_restored']
    final = session.refresh()
    report.update({'final_remote_status': final['remote_status'], 'final_gain': final['gain']})
except Exception as error:
    report['error'] = str(error)
finally:
    if restore_needed and session and original_pre is not None:
        try:
            session.set_setting('pre-record', original_pre)
            report['restored_after_failure'] = True
        except Exception as error:
            report['restore_error'] = str(error)
    if session:
        session.close()
path = root / 'docs/hardware-settings-validation-2026-10-26.json'
path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(report, ensure_ascii=False, indent=2))
sys.exit(0 if report['read_passed'] and (not args.write_test or report['write_test_passed'] or 'write_skip_reason' in report) else 1)
