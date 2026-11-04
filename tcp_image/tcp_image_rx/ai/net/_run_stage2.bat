@echo off
cd /d E:\fpga\my_project\tcp_image_rx\ai\net
set PY=E:\fpga\vivado\2025.2.1\Vivado\tps\win64\python-3.13.0\python.exe
%PY% _prof_all.py e2e   > _profall_e2e.log 2>&1
%PY% _prof_all.py sweep > _profall_sweep.log 2>&1
echo STAGE2_DONE > _profall_stage2.done
