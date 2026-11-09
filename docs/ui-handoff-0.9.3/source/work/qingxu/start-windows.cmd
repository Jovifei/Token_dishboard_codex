@echo off
setlocal
if not exist "D:\Python\Python3.14\pythonw.exe" (
  echo Python runtime not found: D:\Python\Python3.14\pythonw.exe
  echo See README.md. No configuration was changed.
  pause
  exit /b 1
)
start "" /D "%~dp0" "D:\Python\Python3.14\pythonw.exe" "%~dp0launch.pyw"
