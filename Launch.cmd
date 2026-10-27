@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0dist\standalone\PCM-A10.exe" (
    start "" "%~dp0dist\standalone\PCM-A10.exe" %*
    exit /b 0
)
if exist "%~dp0dist\fluent\PCM-A10-Fluent.exe" (
    start "" "%~dp0dist\fluent\PCM-A10-Fluent.exe" %*
    exit /b 0
)
echo Desktop is not built. Run: python scripts/build_portable.py
pause
exit /b 1
