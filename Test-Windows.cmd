@echo off
setlocal
set PYTHONUTF8=1
py -3 -B "%~dp0tools\run-windows-tests.py" %*
exit /b %errorlevel%
