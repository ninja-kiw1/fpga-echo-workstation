@echo off
REM ---------------------------------------------------------------------------
REM Launch the TCP image round-trip GUI, or the command-line self-test.
REM
REM NO BLACK CONSOLE BOX by default.
REM
REM   run_ui.vbs    double-click THIS one -- only the GUI window appears
REM   run.bat       also fine, but cmd blinks for a few milliseconds before it
REM                 hands over to run_ui.vbs
REM
REM Args are forwarded to app.py, e.g.
REM   run.bat 192.168.1.254 5060
REM   run.bat --image D:\pics\cat.jpg
REM
REM A console window is kept on purpose when it is actually useful:
REM   --selftest out.png   prints GUI_SELFTEST ... lines, needs stdout
REM   --console            force a console even for a GUI run (debugging)
REM
REM Machine-specific quirks handled here:
REM
REM  1) Which Python: the system-wide Python 3.14 has a broken tkinter
REM     (python313.dll conflict), so we use the Python 3.13 bundled with
REM     Vivado 2025.2.1, whose tkinter works.
REM
REM  2) Tcl/Tk lookup: that Python ships its OWN tcl/tk in the tcl subfolder of
REM     its install dir.  If a shell (e.g. one that inherited Vivado's
REM     environment) already has TCL_LIBRARY pointing at Vivado's Tcl, tkinter
REM     dies with "version conflict for package Tcl: have 8.6.14, need exactly
REM     8.6.13".  So we pin TCL_LIBRARY/TK_LIBRARY to the Python's own copies.
REM
REM  3) No console: pythonw.exe never allocates one, so anything app.py prints
REM     would be lost.  In that mode we redirect stdout+stderr to run_log.txt
REM     next to this file, and run_ui.vbs pops up a message box with the tail
REM     of it if the start fails.
REM
REM ASCII-only on purpose: cmd.exe reads .bat files with the OEM codepage.
REM
REM Environment variables honoured:
REM   NO_PAUSE=1     do not wait for a key press when a console run fails
REM   NO_CONSOLE=0   force a console window even for a GUI run
REM ---------------------------------------------------------------------------
setlocal
set HERE=%~dp0
set PYROOT=E:\fpga\vivado\2025.2.1\Vivado\tps\win64\python-3.13.0
set PY=%PYROOT%\python.exe
set PYW=%PYROOT%\pythonw.exe
set OWN_PYROOT=1

REM This checkout also supports the srtp-vision environment on the current PC.
if not exist "%PY%" (
    set PYROOT=%USERPROFILE%\anaconda3\envs\srtp-vision
)
set PY=%PYROOT%\python.exe
set PYW=%PYROOT%\pythonw.exe

if not exist "%PY%" (
    echo [run] bundled python not found, falling back to "python" on PATH
    set PY=python
    set PYW=pythonw
    set OWN_PYROOT=0
)

if "%OWN_PYROOT%"=="1" (
    if exist "%PYROOT%\tcl\tcl8.6\init.tcl" (
        set TCL_LIBRARY=%PYROOT%\tcl\tcl8.6
        set TK_LIBRARY=%PYROOT%\tcl\tk8.6
    )
    if exist "%PYROOT%\Library\lib\tcl8.6\init.tcl" (
        set TCL_LIBRARY=%PYROOT%\Library\lib\tcl8.6
        set TK_LIBRARY=%PYROOT%\Library\lib\tk8.6
    )
)

REM ---- console or no console? -----------------------------------------------
REM QUIET=1: start app.py with pythonw.exe, output goes to run_log.txt.
set QUIET=1
set HIDDEN=0
if "%~1"=="" goto :args_done
for %%A in (%*) do (
    if /I "%%A"=="--selftest"   set QUIET=0
    if /I "%%A"=="--console"    set QUIET=0
    if /I "%%A"=="--hidden-run" set HIDDEN=1
)
:args_done
if "%NO_CONSOLE%"=="0" set QUIET=0

REM ---- a GUI run is handed to run_ui.vbs so no black box ever shows up ------
if not "%QUIET%"=="1" goto :run_console
if "%HIDDEN%"=="1" goto :run_hidden
wscript //nologo "%HERE%run_ui.vbs" %* --hidden-run
set RC=%ERRORLEVEL%
goto :finish

:run_console
echo [run] python : %PY%
echo [run] app    : %HERE%app.py
"%PY%" "%HERE%app.py" %*
set RC=%ERRORLEVEL%
goto :finish

:run_hidden
set LOG=%HERE%run_log.txt
"%PYW%" "%HERE%app.py" %* >"%LOG%" 2>&1
set RC=%ERRORLEVEL%

:finish
if "%RC%"=="0" goto :bye
echo.
echo [run] exited with error code %RC%
REM hidden runs report through a message box in run_ui.vbs, not here
if "%QUIET%"=="1" goto :bye
if "%NO_PAUSE%"=="1" goto :bye
pause >nul
:bye
exit /b %RC%
