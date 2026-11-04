#!/bin/sh
# 在 FZ3B 板上原生编译 plbram.ko。
# 板载 gcc 7.5.0 (Ubuntu 18.04) 正好配 4.14 内核 —— 完全避开工具链不匹配。
#
# 前置: 内核源码树在 $KDIR (无 .git, 版本号才会等于 4.14.0-xilinx-v2018.3)
set -e

KDIR=${KDIR:-/root/linux-xlnx}
KMOD=${KMOD:-/root/tcp_image_rx/kmod}
WANT=$(uname -r)

echo "=== [0] 前置检查 ==="
[ -d "$KDIR" ] || { echo "ERROR: 找不到内核源码树 $KDIR"; exit 1; }
echo "板载 gcc : $(gcc --version | head -1)"
echo "运行内核 : $WANT"

echo "=== [1] 用当前运行内核的配置 ==="
if [ ! -f "$KDIR/.config" ]; then
    zcat /proc/config.gz > "$KDIR/.config"
    echo "已从 /proc/config.gz 生成 .config"
else
    echo ".config 已存在, 沿用"
fi

echo "=== [2] olddefconfig ==="
make -C "$KDIR" ARCH=arm64 olddefconfig

echo "=== [3] modules_prepare (第一次约 10-30 min, 4x A53) ==="
make -C "$KDIR" ARCH=arm64 modules_prepare

echo "=== [4] 版本自检 (必须 == $WANT) ==="
REL=$(make -C "$KDIR" ARCH=arm64 --no-print-directory kernelrelease | tail -1)
echo "源码树 kernelrelease = $REL"
if [ "$REL" != "$WANT" ]; then
    echo "!!! 版本不一致 -> 模块 vermagic 会对不上, 加载必被拒。先停下来看这里 !!!"
    exit 2
fi
echo "版本 OK"

echo "=== [5] 编 plbram.ko ==="
make -C "$KDIR" M="$KMOD" modules

echo "=== [6] 结果 ==="
ls -l "$KMOD/plbram.ko"
echo "--- vermagic ---"
strings "$KMOD/plbram.ko" | grep -m1 vermagic || true

echo "=== [7] 清理 tarball 省空间 ==="
rm -f /root/linux-xlnx.tar.gz
df -h / | tail -1

echo "BUILD_OK"
