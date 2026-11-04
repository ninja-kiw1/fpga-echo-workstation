@echo off
setlocal
call "%~dp0..\..\..\environment\build-env.bat"
if errorlevel 1 exit /b %ERRORLEVEL%
cd /d "%PLD_RX%\ai\bd"
call "%PLD_VIVADO%\bin\vivado.bat" -mode batch -source "%PLD_RX%\ai\bd\build_ai_sys.tcl" -log "%PLD_RX%/vivado/ai_sys_build.log" -journal "%PLD_RX%\vivado\ai_sys_build.jou"
set "PLD_RC=%ERRORLEVEL%"
echo EXIT=%PLD_RC% > "%PLD_RX%\vivado\ai_sys_exit.txt"
if not "%PLD_RC%"=="0" exit /b %PLD_RC%
exit /b %PLD_RC%
