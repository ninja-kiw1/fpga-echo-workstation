@echo off
rem Exercise the new flags / AI paths with the real fundus image test.ppm.
rem ASCII only: cmd.exe reads .bat with the OEM codepage.
set PYROOT=E:\fpga\vivado\2025.2.1\Vivado\tps\win64\python-3.13.0
set PY=%PYROOT%\python.exe
set UI=E:\fpga\my_project\tcp_image_ui
set OUT=E:\fpga\my_project\tcp_image_rx\ai\net\e2e_out
cd /d %UI%

echo ========== a. mode0 proc+mask+overlay+stats, AI src=raw ==========
"%PY%" imagelink.py 192.168.1.254 5060 test.ppm --mode 0 --flags proc,mask,overlay,stats
echo.

echo ========== b. mode0 mask+stats, AI src=preprocessed ==========
"%PY%" imagelink.py 192.168.1.254 5060 test.ppm --mode 0 --flags mask,stats --ai-src proc --save-mask "%OUT%\test_mask_fromproc.png"
echo.

echo ========== c. mode3 pcolor + mask ==========
"%PY%" imagelink.py 192.168.1.254 5060 test.ppm --mode 3 --flags proc,mask,stats --save-proc "%OUT%\test_mode3_proc.png" --save-mask "%OUT%\test_mode3_mask.png"
echo.

echo ========== d. tighter threshold (0) ==========
"%PY%" imagelink.py 192.168.1.254 5060 test.ppm --mode 0 --flags mask,stats --ai-thr 0
echo.

echo ========== e. legacy v2 header (no flags) ==========
"%PY%" imagelink.py 192.168.1.254 5060 test.ppm --mode 0 --v2
echo.

echo ========== f. stats only, no image ==========
"%PY%" imagelink.py 192.168.1.254 5060 test.ppm --mode 0 --flags noimage,stats
echo TESTPPM_ALL_DONE
