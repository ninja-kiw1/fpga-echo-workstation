#!/bin/sh
# Native (g++) run of the pp_layer testbench, much faster than vitis csim. Run in WSL from any directory:
#   sh test_native.sh [vectors_dir]
set -e
H=$(cd "$(dirname "$0")" && pwd)
RX=$(cd "$H/../.." && pwd)
I=${XILINX_INCLUDE:-/mnt/e/AMD/Xilinx/2025.2/Vitis/include}
B=$(mktemp -d)
gcc -O2 -std=c99 -D_GNU_SOURCE -I"$RX/src" -c "$RX/src/ai_view.c" -o "$B/ai_view.o"
g++ -O2 -std=c++14 -I"$I" -I"$H/src" "$H/tb/test_pp.cpp" "$H/src/pp_layer.cpp" "$B/ai_view.o" -o "$B/test_pp"
"$B/test_pp" "${1:-$H/vectors}"
rm -rf "$B"
