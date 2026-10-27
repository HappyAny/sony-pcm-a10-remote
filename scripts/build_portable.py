"""Build one self-contained Windows EXE, including the private Python backend."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, default=root / 'dist/standalone')
output = parser.parse_args().output.resolve()
env = os.environ.copy()
for key, folder in {
    'DOTNET_CLI_HOME': 'dotnet-home', 'NUGET_HTTP_CACHE_PATH': 'nuget-http',
    'NUGET_PLUGINS_CACHE_PATH': 'nuget-plugins', 'NUGET_SCRATCH': 'nuget-scratch'
}.items():
    env[key] = str(root / 'build' / folder)
env['DOTNET_CLI_TELEMETRY_OPTOUT'] = '1'
subprocess.run([sys.executable, str(root / 'scripts/build_fluent.py'), '--output', str(root / 'build/portable-base')],
               cwd=root, env=env, check=True)
payload = root / 'build/portable-payload/BackendPayload.zip'
payload.parent.mkdir(parents=True, exist_ok=True)
backend = root / 'build/fluent-runtime/PCM-A10-Backend'
with zipfile.ZipFile(payload, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    for path in sorted(backend.rglob('*')):
        if path.is_file():
            archive.write(path, path.relative_to(backend).as_posix())
staging = root / 'build/portable-publish'
subprocess.run([
    'dotnet', 'publish', str(root / 'desktop/PcmA10.Fluent/PcmA10.Fluent.csproj'),
    '-c', 'Release', '-r', 'win-x64', '--self-contained', 'true', '-o', str(staging),
    '-p:RestoreConfigFile=' + str(root / 'desktop/PcmA10.Fluent/NuGet.Portable.Config'),
    '-p:RestorePackagesPath=' + str(root / 'build/nuget'),
    '-p:RuntimeFrameworkVersion=10.0.14', '-p:AssemblyName=PCM-A10',
    '-p:BackendPayloadPath=' + str(payload), '-p:PublishSingleFile=true',
    '-p:IncludeNativeLibrariesForSelfExtract=true', '-p:EnableCompressionInSingleFile=true',
    '-p:DebugType=embedded', '-p:DebugSymbols=false', '-p:PublishTrimmed=false',
    '-p:PublishReadyToRun=false', '-p:UseSharedCompilation=false', '-nodeReuse:false'
], cwd=root, env=env, check=True)
runtime_config = json.loads((root / 'desktop/PcmA10.Fluent/bin/Release/net10.0-windows/win-x64/PCM-A10.runtimeconfig.json').read_text(encoding='utf-8'))
frameworks = runtime_config['runtimeOptions']['includedFrameworks']
assert {item['name'] for item in frameworks} == {'Microsoft.NETCore.App', 'Microsoft.WindowsDesktop.App'}
assert all(item['version'] == '10.0.14' for item in frameworks)
files = [p for p in staging.iterdir() if p.is_file()]
assert [p.name for p in files] == ['PCM-A10.exe'], [p.name for p in files]
output.mkdir(parents=True, exist_ok=True)
target = output / 'PCM-A10.exe'
temporary = output / 'PCM-A10.exe.pending'
shutil.copy2(staging / 'PCM-A10.exe', temporary)
os.replace(temporary, target)
receipt = {
    'executable': str(target.relative_to(root)) if target.is_relative_to(root) else str(target),
    'bytes': target.stat().st_size, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
    'self_contained': True, 'runtime_version': '10.0.14', 'runtime_identifier': 'win-x64',
    'native_libraries_embedded': True, 'python_backend_embedded': True,
    'runtime_install_required': False, 'trimming': False, 'distribution_file_count': 1,
    'included_frameworks': frameworks
}
(root / 'build/portable-build.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(json.dumps(receipt, ensure_ascii=False, indent=2))
