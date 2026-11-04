@echo off
rem Usage: run_hls_elt.bat csim|synth|cosim|export [random_cases]
setlocal
call "%~dp0..\..\..\..\environment\build-env.bat"
if errorlevel 1 exit /b %ERRORLEVEL%
set "ELT_STAGE=%~1"
if "%ELT_STAGE%"=="" set "ELT_STAGE=csim"
if not "%~2"=="" set "ELT_RANDOM=%~2"
set "LOG=%~dp0_e2b_%ELT_STAGE%.log"
call "%PLD_VITIS%\bin\vitis-run.bat" --mode hls --tcl "%~dp0run_hls_elt.tcl" > "%LOG%" 2>&1
set "PLD_RC=%ERRORLEVEL%"
echo EXITCODE %PLD_RC% >> "%LOG%"
exit /b %PLD_RC%
