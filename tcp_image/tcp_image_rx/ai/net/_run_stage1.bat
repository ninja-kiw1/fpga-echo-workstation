@echo off
cd /d E:\fpga\my_project\tcp_image_rx\ai\net
set PY=E:\fpga\vivado\2025.2.1\Vivado\tps\win64\python-3.13.0\python.exe
%PY% _prof_all.py check > _profall_check.log 2>&1
%PY% _prof_all.py state > _profall_state.log 2>&1
%PY% _prof_all.py ai    > _profall_ai.log 2>&1
%PY% _gdma_deploy.py deploy > _gdma_d_deploy.log 2>&1
echo STAGE1_DONE > _profall_stage1.done
