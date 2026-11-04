@echo off
rem Plan A on board: (1) V9b known-good sanity, (2) V10 golden + ap.
rem The loader stops tcp-image-server and refuses to program the PL while the
rem HLS cores are busy (that is what hung the board last time).
set PY=E:\fpga\vivado\2025.2.1\Vivado\tps\win64\python-3.13.0\python.exe
set NET=E:\fpga\my_project\tcp_image_rx\ai\net
set V9B=E:\fpga\my_project\tcp_image_rx\bitstream\ai_sys_xu2_v9b.bin
set V10=E:\fpga\my_project\tcp_image_rx\bitstream\ai_sys_xu2.bin
cd /d "%NET%"
del /q "%NET%\_v10ab.done" 2>nul
echo ===== STEP 1: load V9b (known good) ===== > "%NET%\_v10ab.log"
"%PY%" _board_load_bin.py "%V9B%" >> "%NET%\_v10ab.log" 2>&1
echo. >> "%NET%\_v10ab.log"
echo ===== board-side AI on V9b ===== >> "%NET%\_v10ab.log"
"%PY%" _prof_all.py ai >> "%NET%\_v10ab.log" 2>&1
echo. >> "%NET%\_v10ab.log"
echo ===== STEP 2: load V10 (new) ===== >> "%NET%\_v10ab.log"
"%PY%" _board_load_bin.py "%V10%" >> "%NET%\_v10ab.log" 2>&1
echo. >> "%NET%\_v10ab.log"
echo ===== board-side AI on V10 ===== >> "%NET%\_v10ab.log"
"%PY%" _prof_all.py ai >> "%NET%\_v10ab.log" 2>&1
echo. >> "%NET%\_v10ab.log"
echo ===== restore service ===== >> "%NET%\_v10ab.log"
"%PY%" _board_svc.py start >> "%NET%\_v10ab.log" 2>&1
echo V10AB_DONE > "%NET%\_v10ab.done"
