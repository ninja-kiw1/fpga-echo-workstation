@echo off
set PY=E:\fpga\vivado\2025.2.1\Vivado\tps\win64\python-3.13.0\python.exe
cd /d E:\fpga\my_project\tcp_image_rx\ai\net
"%PY%" _board_load_bin.py %1 > _loadtest.log 2>&1
echo EXIT=%ERRORLEVEL% >> _loadtest.log
