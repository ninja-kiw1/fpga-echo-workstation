# 板端服务端（tcp_image_server）—— 板上 gcc 原生编（FZ3B 上是 gcc 7.5 / aarch64）
#
#   把本目录（src/）整个传到板上，然后在板上：
#       make CC=gcc            # 或直接 make（下面已经把 CC 定成 gcc）
#       make clean
#
# ⚠ 不要加 -j：板上并发编同一目录会编出半截二进制，装上去整板挂死（连 ARP 都不回）。
#   编完先 sha256sum 对一眼再 install。
#
# 只有 bm_sad.c 用 NEON（#if defined(__aarch64__)），所以在 x86 上也能编过（自动走标量路径），
# 只是那样编出来的二进制不能拿到板子上跑。

CC      = gcc
CFLAGS  ?= -O2 -std=gnu99 -Wall -Wno-unused-but-set-variable -Wno-unused-function
LDLIBS  ?= -lpthread -lm -lz
BIN      = tcp_image_server

SRC := $(wildcard *.c)
OBJ := $(SRC:.c=.o)

all: $(BIN)

$(BIN): $(OBJ)
	$(CC) $(CFLAGS) -o $@ $(OBJ) $(LDLIBS)

%.o: %.c
	$(CC) $(CFLAGS) -c -o $@ $<

clean:
	rm -f $(OBJ) $(BIN)

.PHONY: all clean
