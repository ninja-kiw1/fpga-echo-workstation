@echo off
rem A1 deploy: upload src + board-side rebuild + restart service
set PY=E:\fpga\vivado\2025.2.1\Vivado\tps\win64\python-3.13.0\python.exe
set NET=E:\fpga\my_project\tcp_image_rx\ai\net
echo START %DATE% %TIME% > "%NET%\_deploy_a1.log"
"%PY%" "%NET%\_deploy_a1.py" >> "%NET%\_deploy_a1.log" 2>&1
echo EXITCODE %ERRORLEVEL% %DATE% %TIME% >> "%NET%\_deploy_a1.log"
