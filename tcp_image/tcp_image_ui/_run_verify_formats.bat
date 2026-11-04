@echo off
rem Run the PC-side format self-check (Netpbm P1..P7 + round trips).
set PYROOT=E:\fpga\vivado\2025.2.1\Vivado\tps\win64\python-3.13.0
cd /d E:\fpga\my_project\tcp_image_ui
"%PYROOT%\python.exe" _verify_formats.py
echo FORMAT_RC=%ERRORLEVEL%
