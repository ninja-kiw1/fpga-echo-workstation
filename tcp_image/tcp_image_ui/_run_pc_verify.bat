@echo off
rem Run every PC-side self check, then a CLI round trip using a real image file.
set PYROOT=E:\fpga\vivado\2025.2.1\Vivado\tps\win64\python-3.13.0
set PY=%PYROOT%\python.exe
rem UTF-8 so the Chinese status strings survive being redirected to a log
chcp 65001 >nul
set PYTHONUTF8=1
cd /d E:\fpga\my_project\tcp_image_ui

echo ===== _verify_pc.py =====
"%PY%" _verify_pc.py
echo PC_RC=%ERRORLEVEL%

echo.
echo ===== _verify_formats.py =====
"%PY%" _verify_formats.py
echo FMT_RC=%ERRORLEVEL%

echo.
echo ===== _verify_threshold.py =====
"%PY%" _verify_threshold.py
echo THR_RC=%ERRORLEVEL%

echo.
echo ===== _verify_samples.py =====
"%PY%" _verify_samples.py
echo SAMPLE_RC=%ERRORLEVEL%

echo.
echo ===== imagelink.py with a real PNG file =====
"%PY%" imagelink.py 192.168.1.254 5060 "E:\fpga\my_project\tcp_image_rx\ai\net\e2e_out\proc_00_tcp_out.png" --flags proc,stats --save "E:\fpga\my_project\tcp_image_rx\ai\net\e2e_out\from_png.ppm"
echo CLI_RC=%ERRORLEVEL%

echo.
echo ===== imagelink.py with a bundled sample name (samples/im0003.ppm) =====
"%PY%" imagelink.py 192.168.1.254 5060 im0003 --mode 1 --thr auto --flags proc,mask,stats --save "E:\fpga\my_project\tcp_image_rx\ai\net\e2e_out\from_im0003.ppm"
echo CLI_SAMPLE_RC=%ERRORLEVEL%
