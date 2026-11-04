@echo off
REM ---------------------------------------------------------------------------
REM Launch the echo workstation main window (heartapp.py): snapshot / keyframe /
REM live modes. Needs numpy + scipy + OpenCV + Pillow, so it prefers the
REM srtp-vision conda env (the bundled Vivado python has no numpy).
REM
REM   run_heart.bat                          board at 192.168.1.254:5060
REM   run_heart.bat 127.0.0.1 5060           on the board itself (DP monitor)
REM   run_heart.bat --mode live              start in live mode
REM   run_heart.bat --console                keep a console for debugging
REM
REM ASCII-only on purpose: cmd.exe reads .bat files with the OEM codepage.
REM ---------------------------------------------------------------------------
setlocal
set HERE=%~dp0
set PYROOT=%USERPROFILE%\anaconda3\envs\srtp-vision
set PY=%PYROOT%\python.exe
set PYW=%PYROOT%\pythonw.exe
if not exist "%PY%" (
    echo [run_heart] srtp-vision env not found, falling back to "python" on PATH
    set PY=python
    set PYW=pythonw
)
set CONSOLE=0
for %%A in (%*) do (
    if /I "%%A"=="--console" set CONSOLE=1
)
if "%CONSOLE%"=="1" (
    "%PY%" "%HERE%heartapp.py" %*
    exit /b %ERRORLEVEL%
)
start "" "%PYW%" "%HERE%heartapp.py" %*
exit /b 0
