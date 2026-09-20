@echo off
cd /d "%~dp0"
title DocuFlow

where py >nul 2>nul
if not errorlevel 1 goto use_py

where python >nul 2>nul
if errorlevel 1 goto no_python
python launcher.py %*
goto end

:use_py
py -3 launcher.py %*
goto end

:no_python
echo Python 3.11 or newer is required.
echo Download it from https://www.python.org/downloads/
pause
exit /b 1

:end
