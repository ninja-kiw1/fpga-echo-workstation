# tcp_image_rx —— 交叉编译 makefile
#
# 目标板: FZ3B (XAZU3EG-1SFVC784I) 上跑 PetaLinux, 即 ARM64 Linux,
# 因此要用 aarch64 的 **Linux** 交叉编译器。三种来源:
#
#   1) PetaLinux 生成的 SDK(推荐):
#        source <sdk>/environment-setup-aarch64-xilinx-linux
#      之后环境里已经有 CC/CROSS_COMPILE, 直接 `make` 即可。
#   2) 发行版自带: Ubuntu 上 `apt install gcc-aarch64-linux-gnu`, 然后 `make`。
#   3) 直接用 PetaLinux 自带的编译器(见 README 的"手工指定工具链"一节)。
#
# ⚠ 注意: Vitis 安装目录里的 aarch64 工具链通常是 **裸机** 版
#   (aarch64-none-elf), 缺 Linux 的 libc 与系统头文件, **不能** 编本程序。
#   裸机版编出来的 .elf 也跑不了 Linux 的 socket / mmap / /dev/mem。

CROSS_COMPILE ?= aarch64-linux-gnu-
CC            ?= $(CROSS_COMPILE)gcc

TARGET  := tcp_image_server
SRCDIR  := src
OBJDIR  := build

# -D_GNU_SOURCE : 打开 getopt_long / usleep / CLOCK_MONOTONIC 等 GNU 扩展声明
# (源码里也各自 #define 了一份, 双保险)
CFLAGS  := -O2 -Wall -Wextra -std=c99 -D_GNU_SOURCE
LDFLAGS :=
LDLIBS  := -lm          # vessel_morph.c: log / sqrt

SRCS := $(wildcard $(SRCDIR)/*.c)
OBJS := $(patsubst $(SRCDIR)/%.c,$(OBJDIR)/%.o,$(SRCS))

# ---------------------------------------------------------------------------
# 平台相关的建目录 / 删除命令
#
# 背景: Windows 的 cmd.exe 不认 `mkdir -p`, 会把 -p 当成另一个参数, 报
# "子目录或文件 -p 已经存在"; 而 POSIX sh 又不认 cmd 的 `if not exist`。
# 所以必须按实际使用的 shell 分支。
#
# 判别方法: 不要用 $(OS) —— 实测在"被别的程序(如 Vivado 的 Tcl)直接拉起"时,
# OS 环境变量可能是空的, 于是误判成 POSIX 分支。改成测 shell 语义本身:
#     cmd 里  echo "test"  -> "test"  (带引号)
#     sh  里  echo "test"  -> test    (不带引号)
# 这个判别与谁启动 make 无关, 也不依赖环境变量。
# ---------------------------------------------------------------------------
ifeq ($(shell echo "test"),"test")
  # cmd.exe: mkdir 会自动建中间目录
  MKDIR_DIR = if not exist "$(1)" mkdir "$(1)"
  RMDIR_DIR = if exist "$(1)" rmdir /s /q "$(1)"
  DEL_FILE  = if exist "$(1)" del /q "$(1)"
else
  MKDIR_DIR = mkdir -p "$(1)"
  RMDIR_DIR = rm -rf "$(1)"
  DEL_FILE  = rm -f "$(1)"
endif

.PHONY: all clean host help unet_selftest

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CC) $(LDFLAGS) -o $@ $(OBJS) $(LDLIBS)
	@echo "== built: $@ (for aarch64 Linux)"

# 板上整网自检工具: 跑一遍 U-Net 并和 golden 比对(见 test/unet_selftest.c)。
# 只编需要的三个文件 —— 不能把 tcp_image_server.c 一起链进来(它有 main)。
unet_selftest: $(SRCDIR)/unet_ai.c $(SRCDIR)/unet_net_data.c test/unet_selftest.c
	$(CC) $(CFLAGS) -I$(SRCDIR) -o $@ $(SRCDIR)/unet_ai.c $(SRCDIR)/unet_net_data.c test/unet_selftest.c
	@echo "== built: $@ (whole-network self test)"

$(OBJDIR)/%.o: $(SRCDIR)/%.c
	@$(call MKDIR_DIR,$(OBJDIR))
	$(CC) $(CFLAGS) -I$(SRCDIR) -c -o $@ $<

# 用本机 gcc 编一份, 只为在 PC 上做"能否编译通过"的冒烟检查。
# 没有 /dev/mem 也没有 AXI DMA, 不能真的收图, 只能验证语法与 self_test 之前
# 的初始化路径。
host:
	$(MAKE) CC=gcc CROSS_COMPILE= TARGET=$(TARGET)_host OBJDIR=build_host

clean:
	@$(call RMDIR_DIR,$(OBJDIR))
	@$(call RMDIR_DIR,build_host)
	@$(call DEL_FILE,$(TARGET))
	@$(call DEL_FILE,$(TARGET)_host)
	@$(call DEL_FILE,$(TARGET).elf)
	@echo "== cleaned"

help:
	@echo "make                     # 交叉编译 $(TARGET) (aarch64 Linux)"
	@echo "make host                # 用本机 gcc 冒烟编译"
	@echo "make clean"
	@echo "make CROSS_COMPILE=<prefix>  # 指定别的交叉前缀"
	@echo ""
	@echo "源码: $(SRCS)"
