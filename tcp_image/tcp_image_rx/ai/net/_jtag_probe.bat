@echo off
set X=E:\fpga\vivado\2025.2.1\Vivado\bin\xsdb.bat
set T=E:\fpga\my_project\tcp_image_rx\ai\net
cd /d "%T%"
call "%X%" "%T%\_jtag_probe.tcl" > "%T%\_jtag_probe.log" 2>&1
echo EXIT=%ERRORLEVEL% >> "%T%\_jtag_probe.log"
