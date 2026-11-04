#!/bin/sh
# J3(b) follow-up: chrt -f is not permitted in this cgroup -> nice -20; plus J1 prototype under load
set -u
for m in cpu mem; do
  ./hog $m 4 3000 > /dev/null 2>&1 &
  HP=$!
  sleep 3
  c=${m}4
  echo "== cond=$c impl=pl512_nice";  timeout 600 nice -n -20 ./strict_net_test_base fundus512.rgb fundus512.i8 0 512 30 | grep -E "SAMPLE|PASS|FAIL"
  echo "== cond=$c impl=pl128_nice";  timeout 600 nice -n -20 ./strict_net_test_base skin128.rgb skin128.i8 1 128 200 | grep -E "SAMPLE|PASS|FAIL"
  echo "== cond=$c impl=pl512_j1";    UNET_J1OVL=2 timeout 600 ./strict_net_test_j1 fundus512.rgb fundus512.i8 0 512 30 | grep -E "SAMPLE|PASS|FAIL"
  echo "== cond=$c impl=pl512_j1nice"; UNET_J1OVL=2 timeout 600 nice -n -20 ./strict_net_test_j1 fundus512.rgb fundus512.i8 0 512 30 | grep -E "SAMPLE|PASS|FAIL"
  kill $HP; wait $HP 2>/dev/null; pkill -x hog; sleep 2
done
echo "== cond=idle impl=pl512_j1"; UNET_J1OVL=2 timeout 600 ./strict_net_test_j1 fundus512.rgb fundus512.i8 0 512 30 | grep -E "SAMPLE|PASS|FAIL"
echo "== cond=idle impl=pl128_j1"; UNET_J1OVL=2 timeout 600 ./strict_net_test_j1 skin128.rgb skin128.i8 1 128 200 | grep -E "SAMPLE|PASS|FAIL"
