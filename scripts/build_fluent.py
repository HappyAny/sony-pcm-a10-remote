"""Build native WPF Fluent UI and the private recorder backend."""
from pathlib import Path
import argparse
import os
import shutil
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
env = os.environ.copy()
env['DOTNET_CLI_HOME'] = str(root / 'build/dotnet-home')
env['DOTNET_CLI_TELEMETRY_OPTOUT'] = '1'
env['PYINSTALLER_CONFIG_DIR'] = str(root / 'build/pyinstaller-config')
parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, default=root / 'dist/fluent')
output = parser.parse_args().output.resolve()
subprocess.run(['dotnet', 'publish', str(root / 'desktop/PcmA10.Fluent/PcmA10.Fluent.csproj'),
                '-c', 'Release', '-r', 'win-x64', '--self-contained', 'false', '-o', str(output),
                '-p:RestoreConfigFile=' + str(root / 'desktop/PcmA10.Fluent/NuGet.Config'),
                '-p:RestorePackagesPath=' + str(root / 'build/nuget'), '-p:UseSharedCompilation=false', '-nodeReuse:false'], cwd=root, env=env, check=True)
subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onedir', '--console',
                '--name', 'PCM-A10-Backend', '--paths', str(root / 'src'),
                '--workpath', str(root / 'build/fluent-backend'), '--distpath', str(root / 'build/fluent-runtime'),
                '--specpath', str(root / 'build'), str(root / 'backend.py')], cwd=root, env=env, check=True)
shutil.copytree(root / 'build/fluent-runtime/PCM-A10-Backend', output / 'runtime', dirs_exist_ok=True)
shutil.copy2(root / 'LICENSE', output / 'LICENSE')
shutil.copy2(root / 'THIRD_PARTY_NOTICES.md', output / 'THIRD_PARTY_NOTICES.md')
shutil.copytree(root / 'docs/licenses', output / 'licenses', dirs_exist_ok=True)
print('Fluent desktop:', output / 'PCM-A10-Fluent.exe')
