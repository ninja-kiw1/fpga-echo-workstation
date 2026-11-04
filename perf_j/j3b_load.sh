#!/bin/sh
# J3(b): latency distribution, PL vs CPU inference, idle / 4-core CPU load / 4-core memory load
set -u
CB=/root/cpu_baseline
run_set() {   # $1 = condition label, $2 = extra prefix (e.g. chrt)
  c=$1
  echo "== cond=$c impl=pl512";  timeout 600 ./strict_net_test_base fundus512.rgb fundus512.i8 0 512 60 | grep -E "SAMPLE|PASS|FAIL"
  echo "== cond=$c impl=pl128";  timeout 600 ./strict_net_test_base skin128.rgb skin128.i8 1 128 300 | grep -E "SAMPLE|PASS|FAIL"
  echo "== cond=$c impl=cpu4_512"; OMP_NUM_THREADS=4 timeout 900 ./cpu_unet_lat_fundus $CB/vectors/fundus_512.rgb $CB/vectors/fundus_512.i8 512 512 30 | grep -E "RUN|RESULT"
  echo "== cond=$c impl=cpu4_128"; OMP_NUM_THREADS=4 timeout 600 ./cpu_unet_lat_skin $CB/vectors/skin_128.rgb $CB/vectors/skin_128.i8 128 128 200 | grep -E "RUN|RESULT"
  echo "== cond=$c impl=cpu1_128"; OMP_NUM_THREADS=1 timeout 600 ./cpu_unet_lat_skin $CB/vectors/skin_128.rgb $CB/vectors/skin_128.i8 128 128 60 | grep -E "RUN|RESULT"
  if [ $c != idle ]; then
    echo "== cond=$c impl=pl512_fifo"; timeout 600 chrt -f 50 ./strict_net_test_base fundus512.rgb fundus512.i8 0 512 60 | grep -E "SAMPLE|PASS|FAIL"
    echo "== cond=$c impl=pl128_fifo"; timeout 600 chrt -f 50 ./strict_net_test_base skin128.rgb skin128.i8 1 128 300 | grep -E "SAMPLE|PASS|FAIL"
  fi
}
uptime
# idle part measured separately (j3b_idle.sh)
for m in cpu mem; do
  ./hog $m 4 3000 > hog_$m.out 2>&1 &
  HP=$!
  sleep 3
  echo "== load $m: $(top -b -n 1 | sed -n 3p)"
  run_set ${m}4
  kill $HP; wait $HP 2>/dev/null; pkill -x hog; sleep 2
  cat hog_$m.out
done
uptime
