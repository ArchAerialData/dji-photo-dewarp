@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Launch.ps1" %*
set "dewarp_exit=%ERRORLEVEL%"
if not "%dewarp_exit%"=="0" if "%~1"=="" pause
exit /b %dewarp_exit%
