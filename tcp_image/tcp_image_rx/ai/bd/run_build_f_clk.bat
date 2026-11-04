@echo off
setlocal
rem usage: run_build_f_clk.bat <mhz> <tag> <ips: orig|p6757|...> [--preflight]   (ASCII only)
call "%~dp0..\..\..\..\environment\build-env.bat"
if errorlevel 1 exit /b %ERRORLEVEL%
set VIV=%PLD_VIVADO%\bin\vivado.bat
set TCL=%PLD_RX%\ai\bd\build_ai_sys_f_clk.tcl
set LOG=%PLD_RX%\vivado\ai_sys_%~2_build%~4.log
echo START %DATE% %TIME% > "%LOG%"
if exist "%PLD_RX%\vivado\ai_sys_%~2.done" del /q "%PLD_RX%\vivado\ai_sys_%~2.done"
call "%VIV%" -mode batch -nojournal -source "%TCL%" -tclargs --mhz %~1 --tag %~2 --ips %~3 %~4 >> "%LOG%" 2>&1
set "PLD_RC=%ERRORLEVEL%"
echo EXITCODE %PLD_RC% %DATE% %TIME% >> "%LOG%"
if not "%PLD_RC%"=="0" exit /b %PLD_RC%
if "%~4"=="" echo BUILD_F_CLK_DONE > "%PLD_RX%\vivado\ai_sys_%~2.done"
exit /b %PLD_RC%
