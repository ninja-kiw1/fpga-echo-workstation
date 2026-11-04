@echo off
setlocal
call "%~dp0..\..\..\environment\build-env.bat"
if errorlevel 1 exit /b %ERRORLEVEL%
REM Build FSBL + PMUFW from the XSA with Vitis in batch mode.
REM ASCII-only.  Clear PYTHONPATH/PYTHONHOME (inherited from the MCP session),
REM force UTF-8 -- Vitis create_client fails on Chinese Windows otherwise.
set "PYTHONPATH="
set "PYTHONHOME="
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
chcp 65001 >nul 2>&1
call "%PLD_VITIS%\bin\vitis.bat" -s "%PLD_RX%\boot\vitis_fsbl.py" > "%PLD_RX%\boot\fsbl_stdout.txt" 2>&1
set "PLD_RC=%ERRORLEVEL%"
if not "%PLD_RC%"=="0" exit /b %PLD_RC%
exit /b %ERRORLEVEL%
