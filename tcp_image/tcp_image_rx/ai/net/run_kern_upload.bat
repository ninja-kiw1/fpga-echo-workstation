@echo off
rem upload linux-xlnx tarball to the board and extract it (long: several minutes)
set PY=E:\fpga\vivado\2025.2.1\Vivado\tps\win64\python-3.13.0\python.exe
set NET=E:\fpga\my_project\tcp_image_rx\ai\net
echo START %DATE% %TIME% > "%NET%\_kern_upload.log"
"%PY%" "%NET%\_kern_upload.py" >> "%NET%\_kern_upload.log" 2>&1
echo EXITCODE %ERRORLEVEL% %DATE% %TIME% >> "%NET%\_kern_upload.log"
