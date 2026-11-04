@echo off
rem A/B: reload the OLD bitstream and re-measure with the exact same script.
set PY=E:\fpga\vivado\2025.2.1\Vivado\tps\win64\python-3.13.0\python.exe
set NET=E:\fpga\my_project\tcp_image_rx\ai\net
set BIN=E:\fpga\my_project\tcp_image_rx\bitstream\ai_sys.bin
cd /d %NET%
echo === load OLD bitstream (ai_sys.bin) === > _ab_old.log
"%PY%" _board_load_bin.py "%BIN%" >> _ab_old.log 2>&1
echo. >> _ab_old.log
echo === board-side AI (old bitstream) === >> _ab_old.log
"%PY%" _prof_all.py ai >> _ab_old.log 2>&1
echo. >> _ab_old.log
echo === restore NEW bitstream (ai_sys_xu2.bin) === >> _ab_old.log
"%PY%" _board_load_bin.py E:\fpga\my_project\tcp_image_rx\bitstream\ai_sys_xu2.bin >> _ab_old.log 2>&1
echo AB_DONE > _ab_old.done
