@echo off
setlocal
rem usage: run_hls_f.bat <core> <period_ns> <stage>   (ASCII only)
call "%~dp0..\..\..\..\environment\build-env.bat"
if errorlevel 1 exit /b %ERRORLEVEL%
set "F_CORE=%~1"
set "F_PERIOD=%~2"
set "F_STAGE=%~3"
set "LOG=%~dp0_f_%~1_%~2_%~3.log"
echo START %DATE% %TIME% > "%LOG%"
call "%PLD_VITIS%\bin\vitis-run.bat" --mode hls --tcl "%~dp0run_hls_f.tcl" >> "%LOG%" 2>&1
set "PLD_RC=%ERRORLEVEL%"
echo EXITCODE %PLD_RC% %DATE% %TIME% >> "%LOG%"
exit /b %PLD_RC%
