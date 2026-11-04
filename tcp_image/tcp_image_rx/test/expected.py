"""期望值: 每行用按行号播种的 LCG 生成, 512 行互不相同,
所以"行放错位置"、丢行、错行这类 bug 一定会被逐字节比对抓到。

    s = row * 2654435761                 (32 位)
    每字节: s = s*1664525 + 1013904223    (32 位); 取 (s >> 24) & 0xFF

输出的 EXPECT_FRAME_CRC16 应与 host_test_rx.c 里的同名常量一致。
"""
import os
import sys

# 不生成 __pycache__: 本脚本 import 的是 ../script/send_image.py,
# 那是交付源码的一部分, 不该被测试产物污染。
sys.dont_write_bytecode = True

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, os.pardir, "script"))
import send_image as si  # noqa: E402

M32 = 0xFFFFFFFF
ROW = si.ROW_BYTES

image = bytearray()
for r in range(si.PKT_COUNT):
    s = (r * 2654435761) & M32
    rowb = bytearray(ROW)
    for c in range(ROW):
        s = (s * 1664525 + 1013904223) & M32
        rowb[c] = (s >> 24) & 0xFF
    image += rowb
image = bytes(image)

crc = si.CRC16_INIT
rowcrc = []
for r in range(si.PKT_COUNT):
    chunk = image[r * ROW:(r + 1) * ROW]
    rowcrc.append(si.crc16_ccitt_false(chunk))
    crc = si.crc16_ccitt_false(chunk, crc)

print("EXPECT_FRAME_CRC16 0x%04X" % crc)
for r in (0, 1, 100, 511):
    print("EXPECT_ROW_CRC16 row %3d = 0x%04X" % (r, rowcrc[r]))
print("distinct row CRCs: %d / 512" % len(set(rowcrc)))
print("all rows identical? ", all(image[0:ROW] == image[r * ROW:(r + 1) * ROW]
                                  for r in range(512)))
