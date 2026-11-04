@echo off
rem Demo: auto threshold with pure-black background, with/without excluding it.
rem ASCII only: cmd.exe reads .bat with the OEM codepage.
set PYROOT=E:\fpga\vivado\2025.2.1\Vivado\tps\win64\python-3.13.0
set PY=%PYROOT%\python.exe
set UI=E:\fpga\my_project\tcp_image_ui
set OUT=E:\fpga\my_project\tcp_image_rx\ai\net\e2e_out
cd /d %UI%

echo ========== auto (excludes pure black/white) -> 101 ==========
"%PY%" imagelink.py 192.168.1.254 5060 test_blackborder.ppm --mode 1 --flags proc --save "%OUT%\bb_auto101.png"
echo.

echo ========== same image, forced 54 (what Otsu would say WITHOUT the exclusion) ==========
"%PY%" imagelink.py 192.168.1.254 5060 test_blackborder.ppm --mode 1 --flags proc --thr 54 --save "%OUT%\bb_thr54.png"
echo.

echo ========== second level 131 ==========
"%PY%" imagelink.py 192.168.1.254 5060 test_blackborder.ppm --mode 1 --flags proc --thr 131 --save "%OUT%\bb_thr131.png"
echo BB_DONE
