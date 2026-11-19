@echo off
rem usage: run_hls.bat csim^|synth^|export   (ASCII only)
setlocal
set HLS_STAGE=%~1
if "%HLS_STAGE%"=="" set HLS_STAGE=csim
set XU=2
set ROOT=E:\fpga\my_project\2026pld
set HLSROOT=E:\fpga\vivado\2025.2.1\Vitis
if not exist "%HLSROOT%\bin\vitis-run.bat" exit /b 2
call "%HLSROOT%\.settings64-Vitis_for_HLS.bat" >nul 2>&1
cd /d "%ROOT%\03_build"
set LOG=%ROOT%\03_build\hls_%HLS_STAGE%.log
echo START %DATE% %TIME% > "%LOG%"
call "%HLSROOT%\bin\vitis-run.bat" --mode hls --tcl "%ROOT%\01_src\PL\tcl\run_2026pld.tcl" >> "%LOG%" 2>&1
echo EXITCODE %ERRORLEVEL% %DATE% %TIME% >> "%LOG%"
type "%LOG%" | findstr /C:"EXITCODE" >nul
exit /b %ERRORLEVEL%
