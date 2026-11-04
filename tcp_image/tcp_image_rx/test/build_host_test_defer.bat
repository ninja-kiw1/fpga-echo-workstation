@echo off
REM ---------------------------------------------------------------------------
REM Build and run the host-side test of the deferred bounce memcpy in
REM src/unet_ai.c (GDMA read-back path, 2026-10-07).
REM
REM Needs MinGW gcc on PATH. ASCII-only on purpose (cmd reads .bat as OEM).
REM ---------------------------------------------------------------------------
setlocal
set HERE=%~dp0
set ROOT=%HERE%..
set OUT=%HERE%build
if not exist "%OUT%" mkdir "%OUT%"

echo [1/2] building host_test_defer.exe ...
gcc -std=gnu99 -Wall -Wextra -include "%HERE%shim\shim_extra.h" -I"%HERE%shim" -I"%ROOT%\src" -o "%OUT%\host_test_defer.exe" "%HERE%host_test_defer.c" "%ROOT%\src\unet_net_data.c"
if errorlevel 1 goto fail

echo [2/2] running host_test_defer.exe ...
"%OUT%\host_test_defer.exe"
if errorlevel 1 goto fail

echo.
echo DEFER TEST PASSED
exit /b 0

:fail
echo.
echo DEFER TEST FAILED
exit /b 1
