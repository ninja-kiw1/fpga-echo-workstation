@echo off
setlocal
call "%~dp0..\..\..\environment\build-env.bat"
if errorlevel 1 exit /b %ERRORLEVEL%
rem Export the parallelized conv_layer IP (xu2) into ai/build/conv_ip_xu2/solution1/impl/ip
set TCL=%PLD_RX%\ai\run_conv_ip_export_xu2.tcl
set LOG=%PLD_RX%\ai\conv_ip_xu2_export.log
echo START %DATE% %TIME% > "%LOG%"
if exist "%PLD_RX%\ai\conv_ip_xu2_export.done" del /q "%PLD_RX%\ai\conv_ip_xu2_export.done"
call "%PLD_VITIS%\bin\vitis-run.bat" --mode hls --tcl "%TCL%" >> "%LOG%" 2>&1
set "PLD_RC=%ERRORLEVEL%"
echo EXITCODE %PLD_RC% %DATE% %TIME% >> "%LOG%"
if not "%PLD_RC%"=="0" exit /b %PLD_RC%
echo IPXU2_DONE > "%PLD_RX%\ai\conv_ip_xu2_export.done"
exit /b %PLD_RC%
