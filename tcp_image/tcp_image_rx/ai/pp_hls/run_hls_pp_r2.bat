@echo off
rem r2 variant of pp_layer (both m_axi pointers in one bundle), project ai/build/pp_ip_r2. Usage: run_hls_pp_r2.bat csim|synth|evaluate|cosim
setlocal
call "%~dp0..\..\..\..\environment\build-env.bat"
if errorlevel 1 exit /b %ERRORLEVEL%
set "PP_STAGE=%~1"
if "%PP_STAGE%"=="" set "PP_STAGE=csim"
set "PP_ONE_BUNDLE=1"
set "LOG=%~dp0_pp_r2_%PP_STAGE%.log"
call "%PLD_VITIS%\bin\vitis-run.bat" --mode hls --tcl "%~dp0run_hls_pp.tcl" > "%LOG%" 2>&1
set "PLD_RC=%ERRORLEVEL%"
echo EXITCODE %PLD_RC% >> "%LOG%"
exit /b %PLD_RC%
