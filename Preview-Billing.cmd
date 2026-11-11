@echo off
cd /d "%~dp0"
py -3 -B tools\demo-billing.py
if errorlevel 1 pause
