#!/bin/sh
# J3(b) idle part only (no load generator): PL vs CPU latency distribution
set -u
CB=/root/cpu_baseline
c=idle
uptime
echo "== cond=$c impl=pl512";  timeout 600 ./strict_net_test_base fundus512.rgb fundus512.i8 0 512 60 | grep -E "SAMPLE|PASS|FAIL"
echo "== cond=$c impl=pl128";  timeout 600 ./strict_net_test_base skin128.rgb skin128.i8 1 128 300 | grep -E "SAMPLE|PASS|FAIL"
echo "== cond=$c impl=cpu4_512"; OMP_NUM_THREADS=4 timeout 900 ./cpu_unet_lat_fundus $CB/vectors/fundus_512.rgb $CB/vectors/fundus_512.i8 512 512 30 | grep -E "RUN|RESULT"
echo "== cond=$c impl=cpu4_128"; OMP_NUM_THREADS=4 timeout 600 ./cpu_unet_lat_skin $CB/vectors/skin_128.rgb $CB/vectors/skin_128.i8 128 128 200 | grep -E "RUN|RESULT"
echo "== cond=$c impl=cpu1_128"; OMP_NUM_THREADS=1 timeout 600 ./cpu_unet_lat_skin $CB/vectors/skin_128.rgb $CB/vectors/skin_128.i8 128 128 60 | grep -E "RUN|RESULT"
uptime
