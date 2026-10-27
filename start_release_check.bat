@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set "release_python=%DEEPYIELD_PYTHON%"
if not defined release_python if exist "%~dp0.venv\Scripts\python.exe" set "release_python=%~dp0.venv\Scripts\python.exe"
if not defined release_python set "release_python=%USERPROFILE%\.conda\envs\deepyield\python.exe"
if not exist "%release_python%" (
  echo Python not found. Run setup.bat or set DEEPYIELD_PYTHON.
  pause
  exit /b 1
)
"%release_python%" "%~dp0deploy\start_release_check.py" %*
set "release_exit=%errorlevel%"
if not "%release_exit%"=="0" pause
exit /b %release_exit%
