@echo off
setlocal
cd /d "%~dp0"
where pyw >nul 2>nul
if %errorlevel%==0 (
  start "" pyw -3 "%~dp0launch.pyw"
  exit /b 0
)
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw "%~dp0launch.pyw"
  exit /b 0
)
where py >nul 2>nul
if %errorlevel%==0 (
  start "" py -3 "%~dp0launch.pyw"
  exit /b 0
)
echo Install Python 3.11 or newer with Tcl/Tk support, then try again.
echo No Codex configuration or knowledge files were changed.
pause
exit /b 1
