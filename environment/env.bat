@echo off
rem Called by launchers; the caller owns SETLOCAL.
for %%I in ("%~dp0..") do set "PLD_ROOT=%%~fI"
if not defined PLD_PYTHON set "PLD_PYTHON=%PLD_ROOT%\.venv\Scripts\python.exe"
rem This PC has no PLD\.venv; fall back to the skin venv (numpy/torch/paramiko).
if not exist "%PLD_PYTHON%" if exist "%PLD_ROOT%\skin\.venv\Scripts\python.exe" set "PLD_PYTHON=%PLD_ROOT%\skin\.venv\Scripts\python.exe"
if not exist "%PLD_PYTHON%" (
  echo ERROR: Project Python missing. Create .venv first; see environment\LOCAL_SETUP.md.
  exit /b 2
)
set "PLD_RX="
for /f "delims=" %%L in ('""%PLD_PYTHON%" "%~dp0local_config.py""') do %%L
if not defined PLD_RX exit /b 2
exit /b 0
