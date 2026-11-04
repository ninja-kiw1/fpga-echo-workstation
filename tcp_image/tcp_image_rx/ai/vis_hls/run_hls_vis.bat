@echo off
rem Usage: run_hls_vis.bat csim|synth|evaluate (evaluate = csynth + Vivado out-of-context synthesis)
setlocal
call "%~dp0..\..\..\..\environment\build-env.bat"
if errorlevel 1 exit /b %ERRORLEVEL%
set "VIS_STAGE=%~1"
if "%VIS_STAGE%"=="" set "VIS_STAGE=csim"
set "LOG=%~dp0_vis_%VIS_STAGE%.log"
call "%PLD_VITIS%\bin\vitis-run.bat" --mode hls --tcl "%~dp0run_hls_vis.tcl" > "%LOG%" 2>&1
set "PLD_RC=%ERRORLEVEL%"
echo EXITCODE %PLD_RC% >> "%LOG%"
exit /b %PLD_RC%
