@echo off
rem Double-click: opens Vivado 2025.2 with the f150 block design (read-only).
call E:\AMD\Xilinx\2025.2\Vivado\bin\vivado.bat -mode tcl -nojournal -nolog -source "%~dp0show_bd_f150.tcl"
