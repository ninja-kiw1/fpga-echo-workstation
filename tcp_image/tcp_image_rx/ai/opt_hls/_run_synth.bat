@echo off
setlocal
call "%~dp0..\..\..\..\environment\build-env.bat"
if errorlevel 1 exit /b %ERRORLEVEL%
cd /d "%PLD_RX%\ai\opt_hls"
set HLS_STAGE=synth
set XU=1
if exist _synth.done del /q _synth.done
call "%PLD_VITIS%\bin\vitis-run.bat" --mode hls --tcl run_conv_par.tcl > _hls_1_synth.log 2>&1
set "PLD_RC=%ERRORLEVEL%"
if not "%PLD_RC%"=="0" exit /b %PLD_RC%
set XU=2
call "%PLD_VITIS%\bin\vitis-run.bat" --mode hls --tcl run_conv_par.tcl > _hls_2_synth.log 2>&1
set "PLD_RC=%ERRORLEVEL%"
if not "%PLD_RC%"=="0" exit /b %PLD_RC%
echo SYNTH_DONE > _synth.done
exit /b %PLD_RC%
