# kbuild 用的 makefile —— 必须和内核源码树一起用。
#
# 用法(在板上, 内核源码已就位):
#     make -C /root/linux-xlnx M=/root/tcp_image_rx/kmod modules
#
# 交叉编译(在 PC 上, 若有匹配的旧 GCC):
#     make -C <linux-xlnx> ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
#          M=<kmod目录> modules

obj-m += plbram.o
obj-m += gdmatest.o
obj-m += plbdma.o
