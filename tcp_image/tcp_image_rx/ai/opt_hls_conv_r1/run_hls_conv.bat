@echo off
rem Usage: run_hls_conv.bat csim|synth|cosim|export [cosim_cases]
setlocal
call "%~dp0..\..\..\..\environment\build-env.bat"
if errorlevel 1 exit /b %ERRORLEVEL%
set "CONV_STAGE=%~1"
if "%CONV_STAGE%"=="" set "CONV_STAGE=csim"
if not "%~2"=="" set "CONV_CASES=%~2"
set "LOG=%~dp0_r1_%CONV_STAGE%.log"
call "%PLD_VITIS%\bin\vitis-run.bat" --mode hls --tcl "%~dp0run_hls_conv.tcl" > "%LOG%" 2>&1
set "PLD_RC=%ERRORLEVEL%"
echo EXITCODE %PLD_RC% >> "%LOG%"
exit /b %PLD_RC%
