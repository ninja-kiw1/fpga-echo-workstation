@echo off
setlocal
call "%~dp0..\..\..\environment\build-env.bat"
if errorlevel 1 exit /b %ERRORLEVEL%
set "PYTHONPATH="
set "PYTHONHOME="
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
chcp 65001 >nul 2>&1
call "%PLD_VITIS%\bin\vitis.bat" -s "%PLD_RX%\boot\vitis_probe.py" > "%PLD_RX%\boot\probe_stdout.txt" 2>&1
set "PLD_RC=%ERRORLEVEL%"
if not "%PLD_RC%"=="0" exit /b %PLD_RC%
exit /b %ERRORLEVEL%
