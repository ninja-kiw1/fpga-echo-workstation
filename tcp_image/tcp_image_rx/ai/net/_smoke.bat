@echo off
set PY=E:\fpga\vivado\2025.2.1\Vivado\tps\win64\python-3.13.0\python.exe
cd /d E:\fpga\my_project\tcp_image_ui
"%PY%" imagelink.py 192.168.1.254 5060 test.ppm --mode 0 --flags proc,mask,overlay,stats --ai-res 512 --repeat 1 > E:\fpga\my_project\tcp_image_rx\ai\net\_final_smoke.log 2>&1
echo SMOKE_DONE >> E:\fpga\my_project\tcp_image_rx\ai\net\_final_smoke.log
