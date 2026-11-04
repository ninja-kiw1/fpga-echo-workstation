#!/bin/sh
# load_ai_sys.sh -- 开机把我们的 ai_sys 比特流烧进 PL。
#
# 背景: 板子出厂 boot.bin 里带的是**厂商比特流**, 上电后 PL 是厂商设计;
# 我们要跑自己的设计, 就得在开机时用 fpga_manager 重新烧一次(不是改 boot.bin
# —— 那条路以前把板子搞砖过, 不走)。这个脚本由 ai-sys-bitstream.service 调用,
# 排 plbram / tcp-image-server 之前。
#
# 不依赖任何临时目录, 幂等, 任何一步失败都只打日志不中断启动。

BIN=/lib/firmware/ai_sys.bin
MGR=/sys/class/fpga_manager/fpga0

echo "ai-sys: start"
[ -f "$BIN" ] || { echo "ai-sys: ERROR no $BIN"; exit 0; }
[ -w "$MGR/firmware" ] || { echo "ai-sys: ERROR no $MGR/firmware"; exit 0; }

# 厂商的 fpgadrv 占着这块 PL, 先卸掉(没装就算了)
if grep -q '^fpgadrv' /proc/modules 2>/dev/null; then
    echo "ai-sys: rmmod fpgadrv"
    rmmod fpgadrv 2>/dev/null || true
fi

echo "ai-sys: writing ai_sys.bin to $MGR/firmware"
echo ai_sys.bin > "$MGR/firmware" || true
echo "ai-sys: state = $(cat $MGR/state 2>/dev/null)"

# 验一下烧进去的到底是不是我们的设计:
#   img_proc_axil 的 VERSION 在 0xA0000010, 我们的值 = 0x494D5003(整帧缓冲) 或 0x494D5004(行缓冲, 2026-10-13 r1)
# (0xA0000000 这个地址在两套设计里都有从机, 读它是安全的; 绝不盲写。)
python3 - <<'PYEOF'
import mmap, os, struct
try:
    fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
    m = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED,
                  mmap.PROT_READ | mmap.PROT_WRITE, offset=0xA0000000)
    v = struct.unpack_from("<I", m, 0x10)[0]
    print("ai-sys: VERSION @0xA0000010 = 0x%08X  %s"
          % (v, "OK (our design, %s)" % ("line buffer" if v == 0x494D5004 else "frame buffer")
             if v in (0x494D5003, 0x494D5004) else "MISMATCH (not our design!)"))
except Exception as e:
    print("ai-sys: verify failed: %s" % e)
PYEOF

echo "ai-sys: done"
exit 0
