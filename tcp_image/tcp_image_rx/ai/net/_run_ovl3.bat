@echo off
cd /d E:\fpga\my_project\tcp_image_rx\ai\net
set PY=E:\fpga\vivado\2025.2.1\Vivado\tps\win64\python-3.13.0\python.exe
%PY% _prof_all.py check > _profall_check.log 2>&1
%PY% _prof_all.py ovl3  > _profall_ovl3.log 2>&1
echo OVL3_DONE > _profall_ovl3.done
