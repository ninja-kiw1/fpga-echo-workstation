@echo off
setlocal
call "%~dp0..\..\..\environment\build-env.bat"
if errorlevel 1 exit /b %ERRORLEVEL%
REM Detached full rebuild for the full-PS-config (boot.bin) design.
REM ASCII-only for cmd.exe.
REM Clear PYTHONPATH: the MCP-hosted Vivado session exports a very long one,
REM which makes child Vivado runs fail with "Could not open 'C' for writing"
REM (tclapp::load_apps / Python).
set "PYTHONPATH="
set "PYTHONHOME="
call "%PLD_VIVADO%\bin\vivado.bat" -mode batch -source "%PLD_RX%\vivado\run_boot_build.tcl" -log "%PLD_RX%\vivado\boot_build.log" -journal "%PLD_RX%\vivado\boot_build.jou"
set "PLD_RC=%ERRORLEVEL%"
if not "%PLD_RC%"=="0" exit /b %PLD_RC%
exit /b %ERRORLEVEL%
