#!/bin/sh
# cpu_load.sh SECONDS -- system-wide busy share and tcp_image_server CPU time over an interval
pid=$(pgrep -x tcp_image_serve | head -1)
a=$(head -1 /proc/stat)
p1=$(awk '{print $14+$15}' /proc/$pid/stat)
sleep "$1"
b=$(head -1 /proc/stat)
p2=$(awk '{print $14+$15}' /proc/$pid/stat)
echo "$a"
echo "$b"
echo "$p1 $p2 $(getconf CLK_TCK)"
