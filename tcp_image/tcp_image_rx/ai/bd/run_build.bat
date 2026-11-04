@echo off
setlocal
call "%~dp0..\..\..\..\environment\build-env.bat"
if errorlevel 1 exit /b %ERRORLEVEL%
rem A1 rebuild: tensor BRAM 128KB -> 256KB (build_ai_sys.tcl)
rem ASCII only, no & | < > characters.
set VIV=%PLD_VIVADO%\bin\vivado.bat
set TCL=%PLD_RX%\ai\bd\build_ai_sys.tcl
set LOG=%PLD_RX%\vivado\ai_sys_build.log
echo START %DATE% %TIME% > "%LOG%"
call "%VIV%" -mode batch -source "%TCL%" >> "%LOG%" 2>&1
set "PLD_RC=%ERRORLEVEL%"
echo EXITCODE %PLD_RC% %DATE% %TIME% >> "%LOG%"
if not "%PLD_RC%"=="0" exit /b %PLD_RC%
exit /b %PLD_RC%
