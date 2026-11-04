@echo off
setlocal
call "%~dp0..\..\..\..\environment\build-env.bat"
if errorlevel 1 exit /b %ERRORLEVEL%
rem r2 block-design preflight only (IP catalog + BD validate, no synthesis). ASCII only.
set VIV=%PLD_VIVADO%\bin\vivado.bat
set TCL=%PLD_RX%\ai\bd\build_ai_sys_xu2dp_e2b_pp_r2.tcl
set LOG=%PLD_RX%\vivado\ai_sys_xu2dp_e2b_pp_r2_preflight.log
call "%VIV%" -mode batch -source "%TCL%" -tclargs --preflight > "%LOG%" 2>&1
set "PLD_RC=%ERRORLEVEL%"
echo EXITCODE %PLD_RC% >> "%LOG%"
exit /b %PLD_RC%
