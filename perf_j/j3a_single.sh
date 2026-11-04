#!/bin/sh
# J3 single-core口径 on f150: same-board CPU U-Net (cpu_baseline binaries of 10-08, bit-exact every run), 1 thread, idle.
# PL side = strict_net_test_v517 idle numbers from j3b_f150.sh / j1_f150_ab.sh (PS uses 1 core while the PL computes).
set -u
CB=/root/cpu_baseline
cd $CB
for impl in blocked neon plain; do
  case $impl in blocked) B=./cpu_unet_fundus; X="";; neon) B=./cpu_unet_fundus; X=--noblock;; plain) B=./cpu_unet_fundus_plain; X="";; esac
  n512=3; [ $impl = plain ] && n512=2
  echo "== cond=idle impl=cpu1_${impl}_512"; OMP_NUM_THREADS=1 timeout 900 $B vectors/fundus_512.rgb vectors/fundus_512.i8 512 512 $n512 $X | grep -E "RUN|RESULT"
  echo "== cond=idle impl=cpu1_${impl}_128f"; OMP_NUM_THREADS=1 timeout 900 $B vectors/fundus_128.rgb vectors/fundus_128.i8 128 128 20 $X | grep -E "RUN|RESULT"
done
