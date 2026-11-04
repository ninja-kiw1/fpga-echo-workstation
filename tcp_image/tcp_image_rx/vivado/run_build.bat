@echo off
setlocal
call "%~dp0..\..\..\environment\build-env.bat"
if errorlevel 1 exit /b %ERRORLEVEL%
REM Detached full rebuild launcher. Kept ASCII-only for cmd.exe.
call "%PLD_VIVADO%\bin\vivado.bat" -mode batch -source "%PLD_RX%\vivado\run_build.tcl" -log "%PLD_RX%\vivado\build.log" -journal "%PLD_RX%\vivado\build.jou"
set "PLD_RC=%ERRORLEVEL%"
if not "%PLD_RC%"=="0" exit /b %PLD_RC%
exit /b %ERRORLEVEL%
