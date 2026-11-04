@echo off
setlocal
call "%~dp0..\..\..\..\environment\build-env.bat"
if errorlevel 1 exit /b %ERRORLEVEL%
rem Rebuild ai_sys with the parallelized conv IP (XU=2) + TRUE DUAL PORT tensor BRAM + eltwise E2b. ASCII only.
set VIV=%PLD_VIVADO%\bin\vivado.bat
set TCL=%PLD_RX%\ai\bd\build_ai_sys_xu2dp_e2b.tcl
set LOG=%PLD_RX%\vivado\ai_sys_xu2dp_e2b_build.log
echo START %DATE% %TIME% > "%LOG%"
if exist "%PLD_RX%\vivado\ai_sys_xu2dp_e2b.done" del /q "%PLD_RX%\vivado\ai_sys_xu2dp_e2b.done"
call "%VIV%" -mode batch -source "%TCL%" >> "%LOG%" 2>&1
set "PLD_RC=%ERRORLEVEL%"
echo EXITCODE %PLD_RC% %DATE% %TIME% >> "%LOG%"
if not "%PLD_RC%"=="0" exit /b %PLD_RC%
echo BUILD_XU2DP_E2B_DONE > "%PLD_RX%\vivado\ai_sys_xu2dp_e2b.done"
exit /b %PLD_RC%
