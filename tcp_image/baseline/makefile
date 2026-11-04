# CPU baseline: same integer U-Net as the FPGA, two parameter sets.
#   board (aarch64, native gcc):  make          -> NEON build + plain-C build
#   PC (WSL x86):                 make pc       -> plain C, auto-vectorised for the host (AVX2)
# Layout: net/unet_net.h, net/fundus_unet_net_data.c, net/skin_unet_net_data.c
CC     ?= gcc
CFLAGS := -O3 -std=c99 -Wall -Wextra -fopenmp -Inet
ARM    := -mcpu=cortex-a53
MODELS := fundus skin

all: $(MODELS:%=cpu_unet_%) $(MODELS:%=cpu_unet_%_plain)

cpu_unet_%: cpu_unet.c net/%_unet_net_data.c net/unet_net.h
	$(CC) $(CFLAGS) $(ARM) -o $@ cpu_unet.c net/$*_unet_net_data.c

cpu_unet_%_plain: cpu_unet.c net/%_unet_net_data.c net/unet_net.h
	$(CC) $(CFLAGS) $(ARM) -DNO_NEON -o $@ cpu_unet.c net/$*_unet_net_data.c

pc: $(MODELS:%=pc_unet_%)

pc_unet_%: cpu_unet.c net/%_unet_net_data.c net/unet_net.h
	$(CC) $(CFLAGS) -march=native -o $@ cpu_unet.c net/$*_unet_net_data.c

clean:
	rm -f cpu_unet_* pc_unet_*

.PHONY: all pc clean
