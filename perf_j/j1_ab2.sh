#!/bin/sh
# base vs J1 spin (1) vs J1 condvar (2); interleaved; strict bit-exact each run
set -u
for round in 1 2; do
  for v in base 1 2; do
    if [ $v = base ]; then E="X=0"; else E="UNET_J1OVL=$v"; fi
    for c in "fundus512 0 512 8" "skin128 1 128 10" "echo128 2 128 10"; do
      set -- $c
      echo "== round=$round variant=$v case=$1"
      env $E timeout 300 ./strict_net_test_j1 $1.rgb $1.i8 $2 $3 $4 | grep -E "SAMPLE|PASS|FAIL|\[j1\]"
    done
  done
done
