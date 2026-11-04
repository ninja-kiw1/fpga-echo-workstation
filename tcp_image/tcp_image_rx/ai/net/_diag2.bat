@echo off
cd /d E:\fpga\my_project\tcp_image_rx\ai\net
echo === python procs (pid, mem, cmdline) === > _diag2.log
wmic process where "name='python.exe'" get processid,workingsetsize,commandline /format:csv >> _diag2.log 2>&1
echo === log mtimes === >> _diag2.log
dir _prof_recon2.log _prof_e2e.log _prof_recon.log >> _diag2.log 2>&1
echo DIAG2_DONE >> _diag2.log
