@echo off
setlocal
call "%~dp0..\..\..\..\environment\build-env.bat"
if errorlevel 1 exit /b %ERRORLEVEL%
cd /d "%~dp0"
call "%PLD_VITIS%\bin\vitis-run.bat" --mode hls --tcl "%~dp0run_csim_small.tcl" > "%~dp0csim_small.log" 2>&1
set "PLD_RC=%ERRORLEVEL%"
echo EXITCODE %PLD_RC% >> "%~dp0csim_small.log"
exit /b %PLD_RC%
