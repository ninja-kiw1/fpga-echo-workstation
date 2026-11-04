@echo off
cd /d E:\fpga\my_project\tcp_image_rx\ai\net
E:\fpga\vivado\2025.2.1\Vivado\tps\win64\python-3.13.0\python.exe _hls_analyze.py > _hls_analyze.log 2>&1
echo EXIT=%ERRORLEVEL% >> _hls_analyze.log
