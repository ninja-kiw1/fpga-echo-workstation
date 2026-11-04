#!/bin/sh
# A/B: deployed driver vs J1 prototype (overlap), interleaved, strict bit-exact each run
set -u
for round in 1 2; do
  for v in base j1; do
    if [ $v = j1 ]; then E="UNET_J1OVL=1"; B=./strict_net_test_j1; else E=""; B=./strict_net_test_base; fi
    for c in "fundus512 0 512 10" "skin128 1 128 10" "echo128 2 128 10" "fundus32 0 32 3"; do
      set -- $c
      echo "== round=$round variant=$v case=$1"
      env $E timeout 300 $B $1.rgb $1.i8 $2 $3 $4 | grep -E "SAMPLE|PASS|FAIL|mismatch=[1-9]|J1|\[j1\]" | tail -14
    done
  done
done
echo "== trace (base, fundus512)"
UNET_GTRACE=/root/perf_j/gtrace_512.txt UNET_PROFILE=1 ./strict_net_test_base fundus512.rgb fundus512.i8 0 512 2 | grep -E "^\[prof\] (total|strips|get)"
UNET_GTRACE=/root/perf_j/gtrace_128.txt ./strict_net_test_base skin128.rgb skin128.i8 1 128 2 | tail -1
UNET_PROFILE=1 UNET_J1OVL=1 ./strict_net_test_j1 fundus512.rgb fundus512.i8 0 512 2 | grep -E "^\[prof\] (total|strips|get)|\[j1\]|PASS"
wc -l gtrace_*.txt
