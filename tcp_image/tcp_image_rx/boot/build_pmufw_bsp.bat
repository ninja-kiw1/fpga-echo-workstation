@echo off
setlocal
call "%~dp0..\..\..\environment\build-env.bat"
if errorlevel 1 exit /b %ERRORLEVEL%
REM Build the PMUFW component's BSP with ninja (MicroBlaze, PMU).
REM Driver headers (xcsudma.h, xsysmonpsu.h, ...) come from the A53 standalone
REM BSP via CPATH, same trick as the FSBL.
set "PYTHONPATH="
set "PYTHONHOME="
set "PATH=%PLD_VITIS%\bin;%PLD_VITIS%\tps\win64\cmake-3.24.2\bin;%PLD_VITIS%\gnu\microblaze\nt\bin;%PATH%"
set "SB=%PLD_RX%/vws_d/boot_plat/psu_cortexa53_0/standalone_psu_cortexa53_0/bsp"
set "CPATH=%SB%/include;%SB%/libsrc/build_configs/gen_bsp/include"
set "LOG=%PLD_RX%\boot\pmufw_bsp_build.log"
echo START > "%LOG%"
"%PLD_VITIS%\bin\ninja.exe" -C "%PLD_RX%\vws_d\boot_plat\zynqmp_pmufw\zynqmp_pmufw_bsp\libsrc\build_configs\gen_bsp" >> "%LOG%" 2>&1
set "PLD_RC=%ERRORLEVEL%"
echo NINJA_RC=%PLD_RC% >> "%LOG%"
if not "%PLD_RC%"=="0" exit /b %PLD_RC%
echo DONE >> "%LOG%"
exit /b %PLD_RC%
