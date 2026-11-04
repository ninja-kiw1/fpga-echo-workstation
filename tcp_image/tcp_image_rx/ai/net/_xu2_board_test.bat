@echo off
rem Board test for a bitstream: load (safe-gated) -> board-side AI selftest -> host sweep.
rem   Usage: _xu2_board_test.bat [bin] [tag]
rem   The loader  stops tcp-image-server  and REFUSES to program the PL while the
rem   HLS cores are busy (that would hang the PS AXI interconnect).
set PY=E:\fpga\vivado\2025.2.1\Vivado\tps\win64\python-3.13.0\python.exe
set NET=E:\fpga\my_project\tcp_image_rx\ai\net
set BIN=%1
if "%BIN%"=="" set BIN=E:\fpga\my_project\tcp_image_rx\bitstream\ai_sys_xu2.bin
set TAG=%2
if "%TAG%"=="" set TAG=xu2
cd /d %NET%
echo === load bitstream (%BIN%) === > _%TAG%_board.log
"%PY%" _board_load_bin.py "%BIN%" >> _%TAG%_board.log 2>&1
echo. >> _%TAG%_board.log
echo === board-side AI: golden + per-node ap  (service must stay STOPPED) === >> _%TAG%_board.log
"%PY%" _prof_all.py ai >> _%TAG%_board.log 2>&1
echo. >> _%TAG%_board.log
echo === start service, then host-side sweep === >> _%TAG%_board.log
"%PY%" _board_svc.py start >> _%TAG%_board.log 2>&1
"%PY%" _prof_all.py sweep >> _%TAG%_board.log 2>&1
echo %TAG%_BOARD_TEST_DONE > _%TAG%_board.done
