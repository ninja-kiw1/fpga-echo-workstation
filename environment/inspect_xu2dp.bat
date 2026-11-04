@echo off
setlocal
call "%~dp0build-env.bat"
if errorlevel 1 exit /b %ERRORLEVEL%
if not exist "%~dp0checks" mkdir "%~dp0checks"
cd /d "%~dp0checks"
call "%PLD_VIVADO%\bin\vivado.bat" -mode batch -source "%~dp0inspect_xu2dp.tcl" -log "%~dp0checks\xu2dp_inspection.log" -journal "%~dp0checks\xu2dp_inspection.jou"
set "PLD_INSPECT_RC=%ERRORLEVEL%"
echo EXITCODE %PLD_INSPECT_RC% %DATE% %TIME% >> "%~dp0checks\xu2dp_inspection.log"
echo INSPECTION_EXITCODE %PLD_INSPECT_RC%
exit /b %PLD_INSPECT_RC%
