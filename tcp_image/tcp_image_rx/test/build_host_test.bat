@echo off
REM ---------------------------------------------------------------------------
REM Build and run the host-side functional test of the receive path.
REM
REM Needs MinGW gcc on PATH. python is optional (only used for expected.py,
REM which independently recomputes the reference CRC).
REM
REM This .bat is deliberately ASCII-only: cmd.exe reads .bat files using the
REM OEM codepage, so non-ASCII comments would turn into garbage commands.
REM ---------------------------------------------------------------------------
setlocal
set HERE=%~dp0
set ROOT=%HERE%..
set OUT=%HERE%build
if not exist "%OUT%" mkdir "%OUT%"

echo [1/3] building host_test_rx.exe ...
gcc -std=c99 -Wall -Wextra -Wno-unused-parameter -D_GNU_SOURCE -D_POSIX_C_SOURCE=200809L -DSIGPIPE=13 -include "%HERE%shim\shim_extra.h" -I"%HERE%shim" -I"%ROOT%\src" -o "%OUT%\host_test_rx.exe" "%HERE%host_test_rx.c" "%ROOT%\src\crc16.c" "%ROOT%\src\axidma.c" "%ROOT%\src\axigpio.c" "%ROOT%\src\axilproc.c"
if errorlevel 1 goto fail

echo [2/3] running expected.py (python-side reference CRC) ...
python "%HERE%expected.py"
echo.

echo [3/3] running host_test_rx.exe ...
"%OUT%\host_test_rx.exe"
if errorlevel 1 goto fail

echo.
echo HOST TEST PASSED
exit /b 0

:fail
echo.
echo HOST TEST FAILED
exit /b 1
