@echo off
rem A/B for the V10 round: reload V9b, measure with the same script, then restore V10.
set PY=E:\fpga\vivado\2025.2.1\Vivado\tps\win64\python-3.13.0\python.exe
set NET=E:\fpga\my_project\tcp_image_rx\ai\net
set V9B=E:\fpga\my_project\tcp_image_rx\bitstream\ai_sys_xu2_v9b.bin
set V10=E:\fpga\my_project\tcp_image_rx\bitstream\ai_sys_xu2.bin
cd /d %NET%
echo === load V9b === > _ab_v9b.log
"%PY%" _board_load_bin.py "%V9B%" >> _ab_v9b.log 2>&1
echo. >> _ab_v9b.log
echo === board-side AI (V9b) === >> _ab_v9b.log
"%PY%" _prof_all.py ai >> _ab_v9b.log 2>&1
echo. >> _ab_v9b.log
echo === restore V10 === >> _ab_v9b.log
"%PY%" _board_load_bin.py "%V10%" >> _ab_v9b.log 2>&1
echo ABV9B_DONE > _ab_v9b.done
