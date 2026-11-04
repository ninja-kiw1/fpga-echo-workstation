@echo off
rem V11 round: (1) V9b reference, (2) V11 golden + ap. Loader gates on HLS idle.
set PY=E:\fpga\vivado\2025.2.1\Vivado\tps\win64\python-3.13.0\python.exe
set NET=E:\fpga\my_project\tcp_image_rx\ai\net
set V9B=E:\fpga\my_project\tcp_image_rx\bitstream\ai_sys_xu2_v9b.bin
set NEW=E:\fpga\my_project\tcp_image_rx\bitstream\ai_sys_xu2.bin
cd /d "%NET%"
del /q "%NET%\_v11ab.done" 2>nul
echo ===== STEP 1: V9b reference ===== > "%NET%\_v11ab.log"
"%PY%" _board_load_bin.py "%V9B%" >> "%NET%\_v11ab.log" 2>&1
"%PY%" _prof_all.py ai >> "%NET%\_v11ab.log" 2>&1
echo. >> "%NET%\_v11ab.log"
echo ===== STEP 2: V11 (new) ===== >> "%NET%\_v11ab.log"
"%PY%" _board_load_bin.py "%NEW%" >> "%NET%\_v11ab.log" 2>&1
"%PY%" _prof_all.py ai >> "%NET%\_v11ab.log" 2>&1
echo. >> "%NET%\_v11ab.log"
echo ===== restore service ===== >> "%NET%\_v11ab.log"
"%PY%" _board_svc.py start >> "%NET%\_v11ab.log" 2>&1
echo V11AB_DONE > "%NET%\_v11ab.done"
