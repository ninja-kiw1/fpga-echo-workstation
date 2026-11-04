#!/bin/sh
# v5.17 loaded latency (j3b_nice.sh style) with strict_net_test_v517 = the deployed src/unet_ai.c (sha 56d6c64d, J1 default)
# nice -20 = what the service now runs at (drop-in 10-nice.conf); nice 0 = old unit, for comparison
set -u
for m in idle cpu mem; do
  HP=""
  if [ $m != idle ]; then ./hog $m 4 3000 > /dev/null 2>&1 & HP=$!; sleep 3; fi
  c=$m; [ $m != idle ] && c=${m}4
  echo "== cond=$c impl=v517_512_nice";  timeout 600 nice -n -20 ./strict_net_test_v517 fundus512.rgb fundus512.i8 0 512 30 | grep -E "SAMPLE|PASS|FAIL"
  echo "== cond=$c impl=v517_128_nice";  timeout 600 nice -n -20 ./strict_net_test_v517 skin128.rgb skin128.i8 1 128 200 | grep -E "SAMPLE|PASS|FAIL"
  echo "== cond=$c impl=v517_512_nice0"; timeout 600 ./strict_net_test_v517 fundus512.rgb fundus512.i8 0 512 30 | grep -E "SAMPLE|PASS|FAIL"
  if [ -n "$HP" ]; then kill $HP; wait $HP 2>/dev/null; pkill -x hog; sleep 2; fi
done
