@echo off
cd /d E:\fpga\my_project\tcp_image_rx\ai\net
E:\fpga\vivado\2025.2.1\Vivado\tps\win64\python-3.13.0\python.exe _gdma_deploy.py %1 > _gdma_d_%1.log 2>&1
echo EXIT=%ERRORLEVEL% >> _gdma_d_%1.log
