@echo off
setlocal
call "%~dp0..\..\..\..\environment\build-env.bat"
if errorlevel 1 exit /b %ERRORLEVEL%
cd /d "%PLD_RX%\ai\opt_hls"
set XU=2
set HLS_STAGE=synth
set PROJ_TAG=%1
if "%PROJ_TAG%"=="" set PROJ_TAG=v6a
set LOG=_%PROJ_TAG%_synth.log
echo START %DATE% %TIME% > %LOG%
if exist _%PROJ_TAG%_synth.done del /q _%PROJ_TAG%_synth.done
call "%PLD_VITIS%\bin\vitis-run.bat" --mode hls --tcl run_conv_par.tcl >> %LOG% 2>&1
set "PLD_RC=%ERRORLEVEL%"
echo EXIT=%PLD_RC% >> %LOG%
if not "%PLD_RC%"=="0" exit /b %PLD_RC%
echo SYNTH_DONE_%PROJ_TAG% > _%PROJ_TAG%_synth.done
exit /b %PLD_RC%
