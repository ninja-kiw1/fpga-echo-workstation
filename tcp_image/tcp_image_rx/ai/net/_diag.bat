@echo off
cd /d E:\fpga\my_project\tcp_image_rx\ai\net
echo === ping === > _diag.log
ping -n 2 -w 2000 192.168.1.254 >> _diag.log 2>&1
echo === python procs === >> _diag.log
tasklist /FI "IMAGENAME eq python.exe" /FO CSV /NH >> _diag.log 2>&1
echo === log sizes === >> _diag.log
dir /B /-C _prof_recon2.log _prof_recon.log >> _diag.log 2>&1
echo DIAG_DONE >> _diag.log
