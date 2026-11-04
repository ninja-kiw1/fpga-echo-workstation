#!/bin/sh
# J3(b) final numbers on f150 (150 MHz) + v5.17: same programs as j3b_v517.sh / j3b_idle.sh / j3b_load.sh.
# PL = strict_net_test_v517 (= deployed src/unet_ai.c, J1 on), bit-exact every run.
# CPU = cpu_unet_lat (NEON tiled, OpenMP), bit-exact every run; also at nice -20 so both sides get the same priority.
set -u
CB=/root/cpu_baseline
uptime
for m in idle cpu mem; do
  HP=""
  if [ $m != idle ]; then ./hog $m 4 3000 > /dev/null 2>&1 & HP=$!; sleep 3; fi
  c=$m; [ $m != idle ] && c=${m}4
  echo "== load $c: $(top -b -n 1 | sed -n 3p)"
  echo "== cond=$c impl=pl512_nice";  timeout 600 nice -n -20 ./strict_net_test_v517 fundus512.rgb fundus512.i8 0 512 30 | grep -E "SAMPLE|PASS|FAIL"
  echo "== cond=$c impl=pl128_nice";  timeout 600 nice -n -20 ./strict_net_test_v517 skin128.rgb skin128.i8 1 128 200 | grep -E "SAMPLE|PASS|FAIL"
  echo "== cond=$c impl=pl512_nice0"; timeout 600 ./strict_net_test_v517 fundus512.rgb fundus512.i8 0 512 30 | grep -E "SAMPLE|PASS|FAIL"
  echo "== cond=$c impl=pl128_nice0"; timeout 600 ./strict_net_test_v517 skin128.rgb skin128.i8 1 128 200 | grep -E "SAMPLE|PASS|FAIL"
  echo "== cond=$c impl=cpu4_512"; OMP_NUM_THREADS=4 timeout 900 ./cpu_unet_lat_fundus $CB/vectors/fundus_512.rgb $CB/vectors/fundus_512.i8 512 512 30 | grep -E "RUN|RESULT"
  echo "== cond=$c impl=cpu4_128"; OMP_NUM_THREADS=4 timeout 600 ./cpu_unet_lat_skin $CB/vectors/skin_128.rgb $CB/vectors/skin_128.i8 128 128 200 | grep -E "RUN|RESULT"
  echo "== cond=$c impl=cpu1_128"; OMP_NUM_THREADS=1 timeout 600 ./cpu_unet_lat_skin $CB/vectors/skin_128.rgb $CB/vectors/skin_128.i8 128 128 60 | grep -E "RUN|RESULT"
  if [ $m != idle ]; then
    echo "== cond=$c impl=cpu4_512_nice"; OMP_NUM_THREADS=4 timeout 900 nice -n -20 ./cpu_unet_lat_fundus $CB/vectors/fundus_512.rgb $CB/vectors/fundus_512.i8 512 512 30 | grep -E "RUN|RESULT"
    echo "== cond=$c impl=cpu4_128_nice"; OMP_NUM_THREADS=4 timeout 600 nice -n -20 ./cpu_unet_lat_skin $CB/vectors/skin_128.rgb $CB/vectors/skin_128.i8 128 128 200 | grep -E "RUN|RESULT"
    echo "== cond=$c impl=cpu1_128_nice"; OMP_NUM_THREADS=1 timeout 600 nice -n -20 ./cpu_unet_lat_skin $CB/vectors/skin_128.rgb $CB/vectors/skin_128.i8 128 128 60 | grep -E "RUN|RESULT"
  fi
  if [ -n "$HP" ]; then kill $HP; wait $HP 2>/dev/null; pkill -x hog; sleep 2; fi
done
uptime
