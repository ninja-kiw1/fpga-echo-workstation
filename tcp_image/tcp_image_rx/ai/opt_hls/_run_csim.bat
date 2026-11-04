@echo off
setlocal
call "%~dp0..\..\..\..\environment\build-env.bat"
if errorlevel 1 exit /b %ERRORLEVEL%
cd /d "%PLD_RX%\ai\opt_hls"
set XU=2
set HLS_STAGE=csim
set PROJ_TAG=%1
if "%PROJ_TAG%"=="" set PROJ_TAG=v8
set LOG=_%PROJ_TAG%_csim.log
echo START %DATE% %TIME% > %LOG%
del /q _%PROJ_TAG%_csim.done 2>nul
if exist _%PROJ_TAG%_csim.done del /q _%PROJ_TAG%_csim.done
call "%PLD_VITIS%\bin\vitis-run.bat" --mode hls --tcl run_conv_par.tcl >> %LOG% 2>&1
set "PLD_RC=%ERRORLEVEL%"
echo EXIT=%PLD_RC% >> %LOG%
if not "%PLD_RC%"=="0" exit /b %PLD_RC%
echo CSIM_DONE_%PROJ_TAG% > _%PROJ_TAG%_csim.done
exit /b %PLD_RC%
