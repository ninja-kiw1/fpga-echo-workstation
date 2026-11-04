"""PYNQ driver for the FZ3B U-Net accelerator (conv_layer + eltwise_layer).

Runs on the board (PYNQ v2.7, Python 3.8, NumPy only) and, with SimDevice from
sim_device.py, on a PC for testing the driver without hardware.

    from unet_fpga import PynqDevice, UNetAccelerator
    acc = UNetAccelerator(PynqDevice('unet_system.bit'), '.')
    logits = acc.run(rgb)        # rgb: uint8 (512, 512, 3); logits: int8 (512, 512)
    mask = logits >= acc.threshold

Data layout (see deployment/unet_conv/README.md): NHWC, int16 containers,
channels padded to 8; parameters are the packed 64-bit words of params/<node>.bin.
Accelerator masters use physical addresses; buffers come from pynq.allocate and
are flushed / invalidated around every call because the HP ports are not coherent.
"""
import os
import time

import numpy as np

LANES = 8
# The integer vessel threshold of the current parameter package
# (lwnet quant_sensitivity summary.json: output_integer_threshold_ge).
DEFAULT_THRESHOLD = -3

# AXI-Lite registers (deployment/*/build/*/impl/ip/drivers/*/src/x*_hw.h).
AP_CTRL, AP_RETURN = 0x00, 0x10
AP_START, AP_DONE = 0x1, 0x2
CONV_REGS = dict(in_=0x18, out=0x24, params=0x30, mode=0x3C, cin=0x44, cout=0x4C,
                 in_h=0x54, in_w=0x5C, relu=0x64, shift=0x6C, qmin=0x74, qmax=0x7C)
ELT_REGS = dict(in_a=0x18, in_b=0x24, out=0x30, mode=0x3C, ca=0x44, cb=0x4C, h=0x54, w=0x5C,
                m0=0x64, m1=0x6C, shift=0x74, qmin=0x7C, qmax=0x84)
IP_NAMES = ('conv_layer_0', 'eltwise_layer_0')


def groups(c):
    return (c + LANES - 1) // LANES


class Node(object):
    """One line of network.txt."""
    FIELDS = ('name', 'kind', 'in0', 'in1', 'mode', 'ca', 'cb', 'h', 'w',
              'relu', 'shift', 'qmin', 'qmax', 'm0', 'm1')

    def __init__(self, line):
        parts = line.split()
        if len(parts) != len(self.FIELDS):
            raise ValueError('bad network.txt line: ' + line)
        for key, value in zip(self.FIELDS, parts):
            setattr(self, key, value if key in ('name', 'kind', 'in0', 'in1') else int(value))
        self.inputs = [n for n in (self.in0, self.in1) if n != '-']
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
        elif self.kind != 'INPUT':
            raise ValueError('unknown node kind ' + self.kind)

    @property
    def shape(self):
        """Packed buffer shape: (h, w, channels padded to 8)."""
        return (self.out_h, self.out_w, groups(self.out_c) * LANES)

    @property
    def dtype(self):
        """Value type of the stored tensor (the container is always int16)."""
        if self.kind == 'INPUT':
            return np.uint8
        return np.int16 if self.qmax > 127 else np.int8


def load_network(path):
    with open(path) as f:
        nodes = [Node(line) for line in f if line.strip() and not line.startswith('#')]
    seen = {}
    for n in nodes:
        for j, name in enumerate(n.inputs):
            if name not in seen:
                raise ValueError('%s: input %s is not defined earlier' % (n.name, name))
            p = seen[name]
            want = n.ca if j == 0 else n.cb
            if (p.out_h, p.out_w, p.out_c) != (n.h, n.w, want):
                raise ValueError('%s: shape mismatch with %s' % (n.name, name))
        seen[n.name] = n
    if not nodes or nodes[0].kind != 'INPUT':
        raise ValueError('network must start with an INPUT node')
    return nodes


class PynqDevice(object):
    """Real hardware: loads the overlay and exposes MMIO + contiguous buffers."""

    def __init__(self, bitfile):
        from pynq import MMIO, Overlay, allocate  # imported here so the module loads on a PC
        self.overlay = Overlay(bitfile)
        self._allocate = allocate
        self.ips = {}
        for name in IP_NAMES:
            info = self.overlay.ip_dict[name]
            self.ips[name] = MMIO(info['phys_addr'], info['addr_range'])

    def allocate(self, shape, dtype):
        return self._allocate(shape=shape, dtype=dtype)

    def ip(self, name):
        return self.ips[name]


class UNetAccelerator(object):
    def __init__(self, device, package_dir, threshold=DEFAULT_THRESHOLD, timeout_s=10.0):
        self.device = device
        self.threshold = threshold
        self.timeout_s = timeout_s
        self.nodes = load_network(os.path.join(package_dir, 'network.txt'))
        self.conv = device.ip(IP_NAMES[0])
        self.elt = device.ip(IP_NAMES[1])
        self.params = {}
        for n in self.nodes:
            if n.kind == 'CONV':
                words = np.fromfile(os.path.join(package_dir, 'params', n.name + '.bin'), dtype='<u8')
                buf = device.allocate((words.size,), np.uint64)
                buf[:] = words
                buf.flush()
                self.params[n.name] = buf
        self._plan_buffers()
        self.last_timings = []

    def _plan_buffers(self):
        """Give every node a buffer, reusing buffers of the same shape once
        their tensor has no remaining consumer. The final output is never reused."""
        last_use = {}
        for i, n in enumerate(self.nodes):
            for name in n.inputs:
                last_use[name] = i
        final = self.nodes[-1].name
        free, self.buffer_of, self.buffers = {}, {}, []
        for i, n in enumerate(self.nodes):
            pool = free.get(n.shape, [])
            if pool:
                buf = pool.pop()
            else:
                buf = self.device.allocate(n.shape, np.int16)
                self.buffers.append(buf)
            self.buffer_of[n.name] = buf
            for name in n.inputs:
                if last_use[name] == i and name != final:
                    shape = self.buffer_of[name].shape
                    free.setdefault(shape, []).append(self.buffer_of[name])
        self.buffer_bytes = sum(b.nbytes for b in self.buffers)

    # ---- register access -------------------------------------------------
    @staticmethod
    def _write(ip, offset, value):
        ip.write(offset, int(value) & 0xFFFFFFFF)

    def _write_addr(self, ip, offset, buf):
        addr = int(buf.physical_address)
        self._write(ip, offset, addr)
        self._write(ip, offset + 4, addr >> 32)

    def _start_and_wait(self, ip, what):
        self._write(ip, AP_CTRL, AP_START)
        deadline = time.perf_counter() + self.timeout_s
        while True:
            # ap_done is clear-on-read: the read that sees it also clears it.
            if ip.read(AP_CTRL) & AP_DONE:
                break
            if time.perf_counter() > deadline:
                raise TimeoutError('%s did not finish within %.1f s' % (what, self.timeout_s))
        status = ip.read(AP_RETURN)
        if status != 0:
            raise RuntimeError('%s rejected its configuration (return %d)' % (what, status))

    def _run_node(self, n):
        out = self.buffer_of[n.name]
        a = self.buffer_of[n.in0]
        if n.kind == 'CONV':
            ip, r = self.conv, CONV_REGS
            self._write_addr(ip, r['in_'], a)
            self._write_addr(ip, r['out'], out)
            self._write_addr(ip, r['params'], self.params[n.name])
            for key, value in (('mode', n.mode), ('cin', n.ca), ('cout', n.cb), ('in_h', n.h),
                               ('in_w', n.w), ('relu', n.relu), ('shift', n.shift),
                               ('qmin', n.qmin), ('qmax', n.qmax)):
                self._write(ip, r[key], value)
        else:
            ip, r = self.elt, ELT_REGS
            b = self.buffer_of[n.in1] if n.in1 != '-' else a  # MaxPool ignores in_b
            self._write_addr(ip, r['in_a'], a)
            self._write_addr(ip, r['in_b'], b)
            self._write_addr(ip, r['out'], out)
            for key, value in (('mode', n.mode), ('ca', n.ca), ('cb', n.cb), ('h', n.h), ('w', n.w),
                               ('m0', n.m0), ('m1', n.m1), ('shift', n.shift),
                               ('qmin', n.qmin), ('qmax', n.qmax)):
                self._write(ip, r[key], value)
        self._start_and_wait(ip, n.name)

    # ---- public API -------------------------------------------------------
    def run(self, rgb, on_node=None):
        """rgb: uint8 (512, 512, 3). Returns int8 logits (512, 512).

        on_node(node, tensor) is called after every node with the valid channels
        of its output (a copy), e.g. to compare against golden tensors.
        Per-node accelerator times (seconds) are left in self.last_timings.
        """
        first = self.nodes[0]
        rgb = np.asarray(rgb)
        if rgb.shape != (first.h, first.w, first.ca) or rgb.dtype != np.uint8:
            raise ValueError('expected uint8 %s, got %s %s' % ((first.h, first.w, first.ca), rgb.dtype, rgb.shape))
        x = self.buffer_of[first.name]
        x[:] = 0
        x[:, :, :first.ca] = rgb
        x.flush()
        if on_node:
            on_node(first, np.array(x[:, :, :first.ca]))
        self.last_timings = []
        for n in self.nodes[1:]:
            t0 = time.perf_counter()
            self._run_node(n)
            self.last_timings.append((n.name, time.perf_counter() - t0))
            if on_node:
                out = self.buffer_of[n.name]
                out.invalidate()
                on_node(n, np.array(out[:, :, :n.out_c]))
        out = self.buffer_of[self.nodes[-1].name]
        out.invalidate()
        return np.array(out[:, :, 0]).astype(np.int8)

    def segment(self, rgb):
        """Convenience: returns (int8 logits, boolean vessel mask)."""
        logits = self.run(rgb)
        return logits, logits >= self.threshold

    def close(self):
        for buf in list(self.params.values()) + self.buffers:
            if hasattr(buf, 'freebuffer'):
                buf.freebuffer()
