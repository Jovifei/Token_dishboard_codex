@echo off
setlocal
py -3 -B "%~dp0tools\run-windows-tests.py" %*
exit /b %errorlevel%
