@echo off
call "%~dp0env.bat"
if errorlevel 1 exit /b %ERRORLEVEL%
set "PYTHONHOME="
set "PYTHONPATH="
set "TCL_LIBRARY="
set "TK_LIBRARY="
"%PLD_PYTHON%" "%~dp0check_toolchain.py"
exit /b %ERRORLEVEL%
