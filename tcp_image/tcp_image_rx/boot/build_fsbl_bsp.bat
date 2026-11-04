@echo off
setlocal
call "%~dp0..\..\..\environment\build-env.bat"
if errorlevel 1 exit /b %ERRORLEVEL%
REM Build the FSBL component's BSP with ninja.
REM The FSBL BSP only carries its own config; the driver headers (xcsudma.h,
REM xsdps.h, xipipsu.h, ...) live in the standalone BSP, so inject that include
REM tree through CPATH (gcc reads CPATH in addition to -I).
set "PYTHONPATH="
set "PYTHONHOME="
set "PATH=%PLD_VITIS%\bin;%PLD_VITIS%\tps\win64\cmake-3.24.2\bin;%PLD_VITIS%\gnu\aarch64\nt\aarch64-none\bin;%PATH%"
set "SB=%PLD_RX%/vws_d/boot_plat/psu_cortexa53_0/standalone_psu_cortexa53_0/bsp"
set "CPATH=%SB%/include;%SB%/libsrc/build_configs/gen_bsp/include"
set "LOG=%PLD_RX%\boot\fsbl_bsp_build.log"
echo START > "%LOG%"
echo CPATH=%CPATH% >> "%LOG%"
"%PLD_VITIS%\bin\ninja.exe" -C "%PLD_RX%\vws_d\boot_plat\zynqmp_fsbl\zynqmp_fsbl_bsp\libsrc\build_configs\gen_bsp" >> "%LOG%" 2>&1
set "PLD_RC=%ERRORLEVEL%"
echo NINJA_RC=%PLD_RC% >> "%LOG%"
if not "%PLD_RC%"=="0" exit /b %PLD_RC%
echo DONE >> "%LOG%"
exit /b %PLD_RC%
