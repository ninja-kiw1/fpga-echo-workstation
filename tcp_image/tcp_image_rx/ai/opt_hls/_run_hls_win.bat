@echo off
setlocal
call "%~dp0..\..\..\..\environment\build-env.bat"
if errorlevel 1 exit /b %ERRORLEVEL%
cd /d "%PLD_RX%\ai\opt_hls"
set XU=%1
set HLS_STAGE=%2
set PROJ_TAG=%3
if "%XU%"=="" set XU=2
if "%HLS_STAGE%"=="" set HLS_STAGE=csim
call "%PLD_VITIS%\bin\vitis-run.bat" --mode hls --tcl run_conv_par.tcl > _win_%XU%_%HLS_STAGE%_%PROJ_TAG%.log 2>&1
set "PLD_RC=%ERRORLEVEL%"
echo EXIT=%PLD_RC% >> _win_%XU%_%HLS_STAGE%_%PROJ_TAG%.log
if not "%PLD_RC%"=="0" exit /b %PLD_RC%
exit /b %PLD_RC%
