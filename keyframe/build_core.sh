#!/bin/sh
# echo_core.dll = the board server's echo C (tcp_image_rx/src) for the PC, with the board makefile's numeric flags:
# -ffp-contract=off for the float code (no fused multiply-add, numpy / board bit for bit), -O3 for the SAD kernel.
# -mfma: the explicit fma() in en_volume_pca must be the hardware, correctly rounded one (as on the A53); MinGW's libm fma
# is not (3,905 of 20,000 EchoNet volumes differed, 10-24). live_crop.c (10-25): the live entry (moving square + exact
# box resize), so the PC can replay what the board does to a captured screen. cardiac_ext.c (10-25): wall motion +
# right-heart checks.
set -e
S=/e/PLD/tcp_image/tcp_image_rx/src
O=$(mktemp -d)
gcc -std=c99 -D_GNU_SOURCE -O2 -Wall -Wextra -Werror -ffp-contract=off -mfma -c $S/echo_num.c -o $O/echo_num.o
for f in kf echo_quality live_crop cardiac_ext; do
  if [ -f $S/$f.c ]; then gcc -std=c99 -D_GNU_SOURCE -O2 -Wall -Wextra -Werror -ffp-contract=off -mfma -I$S -c $S/$f.c -o $O/$f.o; fi
done
gcc -std=c99 -D_GNU_SOURCE -O3 -Wall -Wextra -c $S/bm_sad.c -o $O/bm_sad.o
gcc -shared -o ${OUT:-/e/PLD/keyframe/echo_core.dll} $O/*.o -lm
rm -rf "$O"
echo "built ${OUT:-/e/PLD/keyframe/echo_core.dll}"
