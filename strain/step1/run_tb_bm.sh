#!/bin/sh
# Part E step 1 C-sim (WSL): unchanged vis_layer.cpp, one call per tracking block (masked-tile trick). Needs data/ from dump.py.
set -e
H=$(cd "$(dirname "$0")" && pwd)
RX=/mnt/e/PLD/tcp_image/tcp_image_rx
I=${XILINX_INCLUDE:-/mnt/e/AMD/Xilinx/2025.2/Vitis/include}
g++ -O2 -std=c++14 -I"$I" -I"$RX/ai/vis_hls/src" "$H/tb_bm.cpp" "$RX/ai/vis_hls/src/vis_layer.cpp" -o /tmp/tb_bm
cd "$H" && /tmp/tb_bm data/ | tee tb_bm_out.txt
