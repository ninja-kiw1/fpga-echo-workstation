@echo off
rem A1 verification: run ai/net/run_fullres.py (32x32 regression + 512 vs PLD golden)
set PY=E:\fpga\vivado\2025.2.1\Vivado\tps\win64\python-3.13.0\python.exe
set NET=E:\fpga\my_project\tcp_image_rx\ai\net
if not exist E:\fpga\my_project\tcp_image_rx\build_ai mkdir E:\fpga\my_project\tcp_image_rx\build_ai
echo START %DATE% %TIME% > "%NET%\_fullres_a1.log"
"%PY%" "%NET%\run_fullres.py" >> "%NET%\_fullres_a1.log" 2>&1
echo EXITCODE %ERRORLEVEL% %DATE% %TIME% >> "%NET%\_fullres_a1.log"
