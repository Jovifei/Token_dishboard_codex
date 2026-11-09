@echo off
setlocal
if "%~1"=="" (
  echo Usage: Build-Windows.cmd "new output directory"
  echo Build dependencies: tools\requirements-build.txt
  exit /b 2
)
py -3 -B "%~dp0tools\build-windows.py" --output "%~1"
exit /b %errorlevel%
