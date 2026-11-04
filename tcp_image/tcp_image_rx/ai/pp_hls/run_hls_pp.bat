@echo off
rem Usage: run_hls_pp.bat csim|synth|evaluate (evaluate = csynth + Vivado out-of-context synthesis)
setlocal
call "%~dp0..\..\..\..\environment\build-env.bat"
if errorlevel 1 exit /b %ERRORLEVEL%
set "PP_STAGE=%~1"
if "%PP_STAGE%"=="" set "PP_STAGE=csim"
set "LOG=%~dp0_pp_%PP_STAGE%.log"
call "%PLD_VITIS%\bin\vitis-run.bat" --mode hls --tcl "%~dp0run_hls_pp.tcl" > "%LOG%" 2>&1
set "PLD_RC=%ERRORLEVEL%"
echo EXITCODE %PLD_RC% >> "%LOG%"
exit /b %PLD_RC%
