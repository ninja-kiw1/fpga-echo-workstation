# -*- coding: utf-8 -*-
"""P4-3: 板端整网调度器(在开发板上运行, python3 + 标准库)。

把 30 个节点按 network.txt 顺序跑完:
  张量全部放在 PL 的 128KB BRAM(0xB000_0000), PS 先把权重/输入搬进去,
  配 conv/eltwise 的 AXI-Lite 寄存器 -> ap_start -> 轮询 ap_done -> 读回输出。

分配方案由主机端 plan.json 给定(与 gen_small_vectors.py 一致, 避免两边逻辑漂移)。

用法(板上):  python3 unet_board.py <vectors dir> <out dir>
依赖文件:    vectors dir 里要有 network.txt / input.bin / params / plan.json
"""
import json
import mmap
import os
import struct
import sys
import time

BRAM_BASE = 0xB0000000
CONV_BASE = 0xA0010000
ELT_BASE = 0xA0020000

AP_CTRL, AP_RETURN = 0x00, 0x10
AP_START, AP_DONE = 0x1, 0x2
CONV_REGS = dict(in_=0x18, out=0x24, params=0x30, mode=0x3C, cin=0x44, cout=0x4C,
                 in_h=0x54, in_w=0x5C, relu=0x64, shift=0x6C, qmin=0x74, qmax=0x7C)
ELT_REGS = dict(in_a=0x18, in_b=0x24, out=0x30, mode=0x3C, ca=0x44, cb=0x4C, h=0x54, w=0x5C,
                m0=0x64, m1=0x6C, shift=0x74, qmin=0x7C, qmax=0x84)
LANES = 8

FIELDS = ('name', 'kind', 'in0', 'in1', 'mode', 'ca', 'cb', 'h', 'w',
          'relu', 'shift', 'qmin', 'qmax', 'm0', 'm1')


def groups(c):
    return (c + LANES - 1) // LANES


class Node(object):
    def __init__(self, line):
        parts = line.split()
        if len(parts) != len(FIELDS):
            raise ValueError('bad network.txt line: ' + line)
        for key, value in zip(FIELDS, parts):
            setattr(self, key, value if key in ('name', 'kind', 'in0', 'in1') else int(value))
        self.out_h, self.out_w, self.out_c = self.h, self.w, self.ca
        if self.kind == 'CONV':
            self.out_c = self.cb
            if self.mode == 2:
                self.out_h, self.out_w = 2 * self.h, 2 * self.w
        elif self.kind == 'ELT':
            if self.mode == 1:
                self.out_c = self.ca + self.cb
            elif self.mode == 2:
                self.out_h, self.out_w = self.h // 2, self.w // 2

    @property
    def out_words(self):
        return self.out_h * self.out_w * groups(self.out_c)


class Mem(object):
    """把 /dev/mem 的几段直接映射进来, 提供 32bit 寄存器读写和 BRAM 字节访问。"""

    def __init__(self):
        self.fd = os.open('/dev/mem', os.O_RDWR | os.O_SYNC)
        self.bram = mmap.mmap(self.fd, 0x20000, mmap.MAP_SHARED,
                              mmap.PROT_READ | mmap.PROT_WRITE, offset=BRAM_BASE)
        self.conv = mmap.mmap(self.fd, 0x1000, mmap.MAP_SHARED,
                              mmap.PROT_READ | mmap.PROT_WRITE, offset=CONV_BASE)
        self.elt = mmap.mmap(self.fd, 0x1000, mmap.MAP_SHARED,
                             mmap.PROT_READ | mmap.PROT_WRITE, offset=ELT_BASE)

    # --- registers ---
    def rd(self, reg, off):
        return struct.unpack_from('<I', reg, off)[0]

    def wr(self, reg, off, val):
        struct.pack_into('<I', reg, off, val & 0xFFFFFFFF)

    def wr_addr(self, reg, off, addr):
        self.wr(reg, off, addr & 0xFFFFFFFF)
        self.wr(reg, off + 4, (addr >> 32) & 0xFFFFFFFF)

    # --- BRAM (byte granular) ---
    def bput(self, word_off, data):
        p = word_off * 16
        self.bram[p:p + len(data)] = data

    def bget(self, word_off, nbytes):
        p = word_off * 16
        return bytes(self.bram[p:p + nbytes])

    def start_and_wait(self, reg, what, timeout_s=20.0):
        self.wr(reg, AP_CTRL, AP_START)
        t0 = time.time()
        while True:
            if self.rd(reg, AP_CTRL) & AP_DONE:
                break
            if time.time() - t0 > timeout_s:
                raise RuntimeError('%s did not finish in %.1fs' % (what, timeout_s))
        ret = self.rd(reg, AP_RETURN)
        if ret != 0:
            raise RuntimeError('%s returned status %d (bad config?)' % (what, ret))
        return time.time() - t0


def run_nodes(m, nodes, off_of, param_off, vecdir, plan, outdir, dump):
    """跑一遍节点循环。dump=True 时把每节点输出落盘并打印逐节点耗时。"""
    timings = []
    input_data = None
    for idx, n in enumerate(nodes):
        t0 = time.time()
        a_off = off_of[n.name] if n.kind == 'INPUT' else off_of[n.in0]
        o_off = off_of[n.name]

        if n.kind == 'INPUT':
            if input_data is None:
                input_data = open(os.path.join(vecdir, 'input.bin'), 'rb').read()
            assert len(input_data) == n.out_words * 16
            m.bput(o_off, input_data)
            if dump:
                with open(os.path.join(outdir, n.name + '.bin'), 'wb') as f:
                    f.write(input_data)
                print('[%02d] %-38s INPUT  %dx%dx%d' % (idx, n.name, n.out_h, n.out_w, n.out_c))
            timings.append((n.name, n.kind, time.time() - t0))
            continue

        if n.kind == 'CONV':
            pdata = open(os.path.join(vecdir, 'params', n.name + '.bin'), 'rb').read()
            assert len(pdata) <= plan['max_param_bytes']
            m.bput(param_off, pdata)
            r = CONV_REGS
            m.wr_addr(m.conv, r['in_'], BRAM_BASE + a_off * 16)
            m.wr_addr(m.conv, r['out'], BRAM_BASE + o_off * 16)
            m.wr_addr(m.conv, r['params'], BRAM_BASE + param_off * 16)
            for key, value in (('mode', n.mode), ('cin', n.ca), ('cout', n.cb), ('in_h', n.h),
                               ('in_w', n.w), ('relu', n.relu), ('shift', n.shift),
                               ('qmin', n.qmin), ('qmax', n.qmax)):
                m.wr(m.conv, r[key], value)
            elapsed = m.start_and_wait(m.conv, n.name)
        else:
            b_off = a_off if n.in1 == '-' else off_of[n.in1]
            r = ELT_REGS
            m.wr_addr(m.elt, r['in_a'], BRAM_BASE + a_off * 16)
            m.wr_addr(m.elt, r['in_b'], BRAM_BASE + b_off * 16)
            m.wr_addr(m.elt, r['out'], BRAM_BASE + o_off * 16)
            for key, value in (('mode', n.mode), ('ca', n.ca), ('cb', n.cb), ('h', n.h),
                               ('w', n.w), ('m0', n.m0), ('m1', n.m1), ('shift', n.shift),
                               ('qmin', n.qmin), ('qmax', n.qmax)):
                m.wr(m.elt, r[key], value)
            elapsed = m.start_and_wait(m.elt, n.name)

        # BRAM 里的副本留给下游消费者; 只有 dump 时才回读落盘
        if dump:
            blob = m.bget(o_off, n.out_words * 16)
            with open(os.path.join(outdir, n.name + '.bin'), 'wb') as f:
                f.write(blob)
            print('[%02d] %-38s %-5s  %dx%dx%d  pl=%.1f ms'
                  % (idx, n.name, n.kind, n.out_h, n.out_w, n.out_c, elapsed * 1e3))
        timings.append((n.name, n.kind, elapsed))
    return timings


def main():
    vecdir = sys.argv[1] if len(sys.argv) > 1 else '/root/tcp_image_rx/vectors_small'
    outdir = sys.argv[2] if len(sys.argv) > 2 else '/root/tcp_image_rx/net_out'
    bench = 0
    if len(sys.argv) > 3 and sys.argv[3] == '--bench':
        bench = int(sys.argv[4]) if len(sys.argv) > 4 else 50
    os.makedirs(outdir, exist_ok=True)

    plan = json.load(open(os.path.join(vecdir, 'plan.json')))
    nodes = [Node(l) for l in open(os.path.join(vecdir, 'network.txt')) if l.strip()]
    off_of = {p['name']: p['off_words'] for p in plan['nodes']}
    param_off = plan['param_off_words']
    assert plan['total_words'] <= plan['bram_words'], 'plan exceeds BRAM'

    m = Mem()

    # ---- 开跑前先读一次, 确认 IP 在响应(避免访问无从机地址把 CPU 挂死) ----
    c = m.rd(m.conv, AP_CTRL)
    e = m.rd(m.elt, AP_CTRL)
    print('[probe] conv CTRL=0x%08X ap_idle=%d | elt CTRL=0x%08X ap_idle=%d'
          % (c, (c >> 2) & 1, e, (e >> 2) & 1))
    if not ((c >> 2) & 1) or not ((e >> 2) & 1):
        print('[abort] an accelerator is not idle; wrong bitstream?')
        return 1
    print('[plan] tensor_peak=%d param_off=%d total=%d / %d words'
          % (plan['tensor_peak_words'], param_off, plan['total_words'], plan['bram_words']))

    if bench > 0:
        # 纯调度基准: 只跑节点循环, 不落盘
        t0 = time.time()
        for _ in range(bench):
            run_nodes(m, nodes, off_of, param_off, vecdir, plan, outdir, dump=False)
        dt = time.time() - t0
        print('[bench] python scheduler: %d iters in %.1f ms -> %.2f ms/frame'
              % (bench, dt * 1e3, dt * 1e3 / bench))
        print('NET_DONE')
        return 0

    timings = run_nodes(m, nodes, off_of, param_off, vecdir, plan, outdir, dump=True)
    tot = sum(t[2] for t in timings)
    print('[time] accelerator total %.1f ms over %d nodes' % (tot * 1e3, len(timings)))
    print('NET_DONE')
    return 0


if __name__ == '__main__':
    sys.exit(main())
