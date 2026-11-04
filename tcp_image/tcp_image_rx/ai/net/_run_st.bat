@echo off
cd /d E:\fpga\my_project\tcp_image_rx\ai\net
E:\fpga\vivado\2025.2.1\Vivado\tps\win64\python-3.13.0\python.exe _run_selftest.py %1 %2 %3 %4 > _selftest.log 2>&1
echo EXIT=%ERRORLEVEL% >> _selftest.log
