@echo off
set PY=E:\fpga\vivado\2025.2.1\Vivado\tps\win64\python-3.13.0\python.exe
cd /d E:\fpga\my_project\tcp_image_rx\ai\net
"%PY%" _prof_all.py state > _board_state.log 2>&1
echo STATE_DONE >> _board_state.log
