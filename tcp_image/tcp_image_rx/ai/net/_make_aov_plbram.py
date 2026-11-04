# -*- coding: utf-8 -*-
"""把 A-ov(重叠) 版本的 unet_ai.c 重建出来, 并接上 /dev/plbram(Normal-NC) 映射。

做法: 取存档的 A-ov 版, 施加与"已验证的 plbram 版"相同的 4 处改动:
  1) bram_get 之后加 bram_commit()
  2) unet_init 之前加 map_bram()
  3) unet_init 里 BRAM 的 mmap 改成 map_bram()
  4) 两处组装结束 -> start_ap 之前插入 bram_commit(...)

每处都断言"模式唯一", 不匹配就报错, 不静默改。
"""
import os
import sys

PROJ = r"E:/fpga/my_project/tcp_image_rx"
ARCH = os.path.join(PROJ, "ai", "net", "_unet_ai_aov_overlap_reference.c.txt")
DST = os.path.join(PROJ, "src", "unet_ai.c")

BRAM_GET = '''static inline void bram_get(uint32_t word_off, void *dst, size_t bytes)
{
    uint64_t t0 = now_us();

    memcpy(dst, (const void *)bram_at(word_off), bytes);
    g_get_us   += now_us() - t0;
    g_get_words += bytes >> 4;
}
'''

COMMIT = '''
/* ⚠ Normal-NC(写合并)映射下, 前面写的字可能还在 CPU 的写缓冲里没到 BRAM。
 * 而 AP_START 是走**另一个口**(HPM0 控制口)发出去的, 两条路之间没有顺序保证
 * —— 不处理的话 IP 可能读到旧的输入。这里**读回程序顺序里最后一个写入字**:
 * 同一个 master 对同一个从机的写是保序的, 这次读一旦返回, 它之前的写就都已到 BRAM。
 * 代价是一次 ~200 ns 的读, 每条带一次, 可忽略。 */
static inline void bram_commit(uint32_t word_off)
{
    volatile const uint32_t *p = (volatile const uint32_t *)(const void *)bram_at(word_off);

    (void)p[0];
}
'''

MAP_BRAM = '''/* 张量 BRAM 用哪种映射:
 *   优先 /dev/plbram (plbram.ko 给的 Normal-NC, 实测**写快 ~2.7x**; 读不变,
 *   因为非缓存读本来就是一次完整往返、不流水 —— 见 docs/ai_full_resolution_strip_plan.md §14);
 *   没有就退回 /dev/mem (Device)。UNET_BRAM_MEM=1 可强制走 /dev/mem 做对照。 */
static void *map_bram(uint64_t bram_base, size_t bytes)
{
    int   fd;
    void *p;

    if (getenv("UNET_BRAM_MEM") == NULL) {
        fd = open("/dev/plbram", O_RDWR | O_SYNC);
        if (fd >= 0) {
            p = mmap(NULL, bytes, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
            close(fd);                       /* mmap 之后关 fd, 映射仍有效 */
            if (p != MAP_FAILED) {
                printf("[ai] tensor BRAM via /dev/plbram (Normal-NC)\\n");
                return p;
            }
            printf("[ai] /dev/plbram mmap failed: %s\\n", strerror(errno));
        } else {
            printf("[ai] no /dev/plbram (%s), using /dev/mem\\n", strerror(errno));
        }
    }
    p = mmap(NULL, bytes, PROT_READ | PROT_WRITE, MAP_SHARED, g_fd, (off_t)bram_base);
    if (p != MAP_FAILED) {
        printf("[ai] tensor BRAM via /dev/mem (Device)\\n");
    }
    return p;
}

'''

OLD_MMAP = '''    g_map_bram_v = (uint8_t *)mmap(NULL, BRAM_BYTES, PROT_READ | PROT_WRITE, MAP_SHARED,
                                   g_fd, (off_t)bram_base);'''
NEW_MMAP = '''    g_map_bram_v = (uint8_t *)map_bram(bram_base, BRAM_BYTES);'''

OLD_CONV_SYNC = r'''( *)bram_put\(par_off, unet_params \+ unet_param_off\[idx\], par_bytes\);
( *)g_param_words \+= \(uint64_t\)\(par_bytes >> 4\);
( *)__sync_synchronize\(\);'''
NEW_CONV_SYNC = r'''\1bram_put(par_off, unet_params + unet_param_off[idx], par_bytes);
\2g_param_words += (uint64_t)(par_bytes >> 4);
\3bram_commit(par_off + par_words - 1u);   /* 刷写完成再启动 IP */
\3__sync_synchronize();'''

OLD_ELT_SYNC = r'''( *)\}
( *)__sync_synchronize\(\);
( *)
( *)wr_addr\(g_elt, E_IN_A, g_bram_base \+ \(uint64_t\)0u \* WORD_BYTES\);'''
NEW_ELT_SYNC = r'''\1}
\2bram_commit(in_b_w ? (in_a_w + in_b_w - 1u)
\2                   : ((uint32_t)nin * (uint32_t)rin_a - 1u));
\2__sync_synchronize();
\3
\4wr_addr(g_elt, E_IN_A, g_bram_base + (uint64_t)0u * WORD_BYTES);'''


def main():
    import re

    src = open(ARCH, encoding="utf-8").read()

    plain = [
        ("bram_get -> +bram_commit", BRAM_GET, BRAM_GET + COMMIT),
        ("unet_init -> +map_bram", "int unet_init(uint64_t conv_ctrl, uint64_t elt_ctrl, uint64_t bram_base)",
         MAP_BRAM + "int unet_init(uint64_t conv_ctrl, uint64_t elt_ctrl, uint64_t bram_base)"),
        ("BRAM mmap -> map_bram()", OLD_MMAP, NEW_MMAP),
    ]
    for tag, o, n in plain:
        c = src.count(o)
        if c != 1:
            print("!! pattern %-28s found %d times (need 1)" % (tag, c))
            sys.exit(1)
        src = src.replace(o, n)
        print("   ok  %s" % tag)

    rx = [
        ("conv: commit before start", OLD_CONV_SYNC, NEW_CONV_SYNC),
        ("elt : commit before start", OLD_ELT_SYNC, NEW_ELT_SYNC),
    ]
    for tag, o, n in rx:
        matches = re.findall(o, src)
        if len(matches) != 1:
            print("!! regex  %-28s matched %d times (need 1)" % (tag, len(matches)))
            sys.exit(1)
        src = re.sub(o, n, src)
        print("   ok  %s" % tag)

    open(DST, "w", encoding="utf-8", newline="\n").write(src)
    print("[write] %s  (%d bytes)" % (DST, len(src)))
    print("AOV_PLBRAM_READY")


if __name__ == "__main__":
    main()
