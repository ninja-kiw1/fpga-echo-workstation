@echo off
setlocal
call "%~dp0build-env.bat"
if errorlevel 1 exit /b %ERRORLEVEL%
if not exist "%~dp0checks" mkdir "%~dp0checks"
cd /d "%~dp0checks"
call "%PLD_VIVADO%\bin\vivado.bat" -mode batch -source "%PLD_RX%\ai\bd\build_ai_sys_xu2dp.tcl" -log "%~dp0checks\vivado_preflight.log" -journal "%~dp0checks\vivado_preflight.jou" -tclargs --preflight
set "PLD_RC=%ERRORLEVEL%"
echo EXITCODE %PLD_RC% >> "%~dp0checks\vivado_preflight.log"
exit /b %PLD_RC%
