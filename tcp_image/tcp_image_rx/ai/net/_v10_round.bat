@echo off
rem V10 round on board: (1) A/B old = V9b, (2) load V10 + golden + ap, (3) host sweep.
set NET=E:\fpga\my_project\tcp_image_rx\ai\net
del /q "%NET%\_ab_v9b.done" "%NET%\_xu2_board.done" "%NET%\_v10_round.done" 2>nul
call "%NET%\_ab_v9b.bat"
call "%NET%\_xu2_board_test.bat"
echo. > "%NET%\_v10_round.log"
echo ===== PART 1: V9b (old) ===== >> "%NET%\_v10_round.log"
type "%NET%\_ab_v9b.log" >> "%NET%\_v10_round.log"
echo. >> "%NET%\_v10_round.log"
echo ===== PART 2: V10 (new) ===== >> "%NET%\_v10_round.log"
type "%NET%\_xu2_board.log" >> "%NET%\_v10_round.log"
echo V10_ROUND_DONE > "%NET%\_v10_round.done"
