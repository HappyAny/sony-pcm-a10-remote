"""Open/close the packaged backend twice without changing recorder settings."""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import argparse
import json
import queue
import subprocess
import threading
import time

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--app-dir', type=Path, default=root / 'dist/fluent')
folder = parser.parse_args().app_dir.resolve()
report = {'created_at': datetime.now(ZoneInfo('Asia/Singapore')).isoformat(), 'success': False,
          'paired_again': False, 'recording_commands_sent': False, 'setting_changes_sent': False, 'cycles': []}
address = None

for cycle in range(2):
    child = subprocess.Popen([str(folder/'runtime/PCM-A10-Backend.exe')], cwd=folder, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
    messages = queue.Queue()
    def reader(stream):
        for line in stream:
            messages.put(json.loads(line))
        messages.put({'type': 'eof'})
    thread = threading.Thread(target=reader, args=(child.stdout,), daemon=True)
    thread.start()
    request_id = 0
    connection_phases = []
    def rpc(method, params=None):
        global request_id
        request_id += 1
        identifier = request_id
        child.stdin.write(json.dumps({'id': identifier, 'method': method, 'params': params or {}})+'\n')
        child.stdin.flush()
        deadline = time.monotonic()+45
        while True:
            message = messages.get(timeout=max(0.01, deadline-time.monotonic()))
            if message.get('type') == 'eof':
                raise RuntimeError('打包后端已退出')
            if message.get('type') == 'event' and message.get('data', {}).get('event') == 'connection':
                connection_phases.append(message['data']['phase'])
            if message.get('type') == 'response' and message.get('id') == identifier:
                if not message.get('ok'):
                    raise RuntimeError(message['error']['message'])
                return message['result']
    try:
        ready = messages.get(timeout=10)
        assert ready['type'] == 'ready' and not ready['demo']
        report['backend_version'] = ready['version']
        devices = [device for device in rpc('devices') if device['paired']]
        if len(devices) != 1:
            raise RuntimeError('需要唯一的已配对 PCM-A10')
        if address is not None and devices[0]['address'] != address:
            raise RuntimeError('两次连接的设备不一致')
        address = devices[0]['address']
        connected = rpc('connect', {'address': address})
        closed = rpc('shutdown')
        child.wait(timeout=6)
        report['cycles'].append({'cycle': cycle+1, 'paired': True, 'connected': connected['ready'],
                                  'remote_status': connected['remote_status'], 'battery': connected['battery'],
                                  'gain': connected['gain'], 'closed': closed['closed'],
                                  'ready_after_close': closed['last_state']['ready'],
                                  'status_after_close': closed['last_state']['remote_status'], 'backend_exited': True,
                                  'connection_phases': connection_phases,
                                  'connection_retry_count': connection_phases.count('正在等待录音机重新开放连接…')})
    except Exception as error:
        report['error'] = f'{type(error).__name__}: {error}'
        report['failed_cycle'] = cycle + 1
        report['connection_phases'] = connection_phases
        break
    finally:
        if child.poll() is None:
            try:
                child.stdin.close()
                child.wait(timeout=3)
            except Exception:
                child.kill()
                child.wait(timeout=3)
        thread.join(timeout=1)
    time.sleep(0.3)

report['success'] = len(report['cycles']) == 2 and all(c['closed'] and not c['ready_after_close'] for c in report['cycles'])
path = root/'docs/hardware-reconnect-2026-10-26.json'
path.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(json.dumps(report, ensure_ascii=False, indent=2))
raise SystemExit(0 if report['success'] else 1)
