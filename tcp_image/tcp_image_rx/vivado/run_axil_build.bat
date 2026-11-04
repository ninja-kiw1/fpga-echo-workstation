@echo off
setlocal
call "%~dp0..\..\..\environment\build-env.bat"
if errorlevel 1 exit /b %ERRORLEVEL%
cd /d "%PLD_RX%"
call "%PLD_VIVADO%\bin\vivado.bat" -mode batch -source vivado\run_axil_build.tcl -log vivado\axil_build2.log -journal vivado\axil_build2.jou
set "PLD_RC=%ERRORLEVEL%"
echo EXIT=%PLD_RC% > vivado\axil_build2_done.txt
if not "%PLD_RC%"=="0" exit /b %PLD_RC%
exit /b %PLD_RC%
