@echo off
setlocal
call "%~dp0..\..\..\environment\build-env.bat"
if errorlevel 1 exit /b %ERRORLEVEL%
rem Use the configured vitis-run directly (no inherited Python environment).
cd /d "%PLD_RX%\ai"
call "%PLD_VITIS%\bin\vitis-run.bat" --mode hls --tcl run_conv_synth.tcl > hls_synth.log 2>&1
set "PLD_RC=%ERRORLEVEL%"
echo EXIT=%PLD_RC% > synth_exit.txt
if not "%PLD_RC%"=="0" exit /b %PLD_RC%
exit /b %PLD_RC%
