#!/bin/sh
# J3(c) on f150: deployed src/unet_ai.c (J1 default) vs UNET_NOJ1=1 (serial put/get split); interleaved; strict bit-exact each run
set -u
for round in 1 2; do
  for v in noj1 j1; do
    if [ $v = noj1 ]; then E="UNET_NOJ1=1"; else E="X=0"; fi
    for c in "fundus512 0 512 12" "skin128 1 128 30" "echo128 2 128 30"; do
      set -- $c
      echo "== round=$round variant=$v case=$1"
      env $E timeout 300 ./strict_net_test_v517 $1.rgb $1.i8 $2 $3 $4 | grep -E "SAMPLE|PASS|FAIL|J1"
    done
  done
done
