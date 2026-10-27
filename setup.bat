@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set "setup_python=%DEEPYIELD_PYTHON%"
if not defined setup_python if exist "%USERPROFILE%\.conda\envs\deepyield\python.exe" set "setup_python=%USERPROFILE%\.conda\envs\deepyield\python.exe"
if defined setup_python (
  "%setup_python%" -m venv .venv
) else (
  py -3 -m venv .venv
)
if errorlevel 1 goto failed
"%~dp0.venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo Setup complete. Double-click start_release_check.bat.
pause
exit /b 0
:failed
echo Setup failed. Check Python 3.10+ and package access.
pause
exit /b 1
