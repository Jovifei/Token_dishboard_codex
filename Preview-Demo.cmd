@echo off
setlocal
py -3 -B "%~dp0tools\demo-current.py" --root "%TEMP%\Qingxu-preview-%RANDOM%-%RANDOM%" %*
exit /b %errorlevel%
