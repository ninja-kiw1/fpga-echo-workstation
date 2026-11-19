@echo off
rem usage: build_pl.bat   -- full BD + synth + impl + bitstream (ASCII only)
setlocal
set ROOT=E:\fpga\my_project\2026pld
set VIV=E:\fpga\vivado\2025.2.1\Vivado\bin\vivado.bat
set PLD_BUILD_JOBS=4
if not exist "%VIV%" exit /b 2
cd /d "%ROOT%\03_build"
set LOG=%ROOT%\03_build\vivado_build.log
echo START %DATE% %TIME% > "%LOG%"
call "%VIV%" -mode batch -nojournal -source "%ROOT%\01_src\PL\tcl\build_2026pld.tcl" -tclargs --mhz 150 --tag pld2026 --ips none >> "%LOG%" 2>&1
set RC=%ERRORLEVEL%
echo EXITCODE %RC% %DATE% %TIME% >> "%LOG%"
exit /b %RC%
