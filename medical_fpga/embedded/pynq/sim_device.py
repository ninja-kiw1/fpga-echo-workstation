"""PC-only stand-in for PynqDevice: emulates the two IPs' AXI-Lite registers and
physical memory, computing with the NumPy references of deployment/unet_conv and
deployment/unet_eltwise. It exercises the whole driver (register offsets, signed
values, 64-bit addresses, packing, buffer reuse) but says nothing about timing.
"""
import importlib.util
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DEPLOY = os.path.join(HERE, '..', '..', 'deployment')


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


conv_ref = _load('conv_ref', os.path.join(DEPLOY, 'unet_conv', 'generate_vectors.py'))
elt_ref = _load('elt_ref', os.path.join(DEPLOY, 'unet_eltwise', 'generate_vectors.py'))
LANES = 8


class SimBuffer(np.ndarray):
    """ndarray with the PynqBuffer methods the driver uses."""
    physical_address = 0

    def flush(self):
        pass

    def invalidate(self):
        pass

    def freebuffer(self):
        pass


def _s32(v):
    return v - (1 << 32) if v & 0x80000000 else v


class SimIP(object):
    def __init__(self, memory, compute):
        self.regs = {}
        self.memory = memory
        self.compute = compute
        self.done = False

    def write(self, offset, value):
        if not 0 <= value <= 0xFFFFFFFF:
            raise ValueError('register value must be an unsigned 32-bit int, got %r' % value)
        self.regs[offset] = value
        if offset == 0x00 and value & 1:
            self.regs[0x10] = self.compute(self)
            self.done = True

    def read(self, offset):
        if offset == 0x00:
            value = 0x6 if self.done else 0x4   # ap_done | ap_idle
            self.done = False                   # ap_done is clear-on-read
            return value
        return self.regs.get(offset, 0)

    def reg(self, offset):
        return _s32(self.regs[offset])

    def buffer(self, offset):
        addr = self.regs[offset] | (self.regs[offset + 4] << 32)
        if addr not in self.memory:
            raise ValueError('register 0x%02x holds 0x%x, which is not the base of any buffer' % (offset, addr))
        return self.memory[addr]


def _unpack(buf, c):
    h, w, _ = buf.shape
    return np.array(buf[:, :, :c], dtype=np.int64).reshape(h, w, c)


def _store(buf, values):
    buf[:] = 0
    buf[:, :, :values.shape[2]] = values


def _reg(ip, offset):
    return ip.reg(offset) if offset in ip.regs else 0


def run_conv(ip):
    mode, cin, cout = _reg(ip, 0x3C), _reg(ip, 0x44), _reg(ip, 0x4C)
    in_h, in_w = _reg(ip, 0x54), _reg(ip, 0x5C)
    relu, shift, qmin, qmax = _reg(ip, 0x64), _reg(ip, 0x6C), _reg(ip, 0x74), _reg(ip, 0x7C)
    # Same checks as conv_layer.cpp: reject without touching memory.
    limit = 256 if mode == 2 else 512
    if not (0 <= mode <= 2 and 1 <= cin <= 32 and 1 <= cout <= 32 and 1 <= in_h <= limit and
            1 <= in_w <= limit and relu in (0, 1) and 0 <= shift <= 31 and -32768 <= qmin <= qmax <= 32767):
        return 1
    taps = conv_ref.TAPS[mode]
    cig, cog = conv_ref.groups(cin), conv_ref.groups(cout)
    words = np.asarray(ip.buffer(0x30), dtype=np.uint64)
    nw = taps * cog * cig * LANES
    w = words[:nw].view(np.int8).reshape(taps, cog, cig, LANES, LANES)
    w_taps = w.transpose(0, 1, 3, 2, 4).reshape(taps, cog * LANES, cig * LANES)[:, :cout, :cin]
    per_ch = words[nw:nw + 3 * cog * LANES].view(np.int64).reshape(3, cog * LANES)[:, :cout]
    x = _unpack(ip.buffer(0x18), cin)
    y = conv_ref.layer_reference(x, w_taps, per_ch[0], per_ch[1], per_ch[2], mode, bool(relu), shift, qmin, qmax)
    _store(ip.buffer(0x24), y)
    return 0


def run_eltwise(ip):
    mode, ca, cb, h, w = (_reg(ip, o) for o in (0x3C, 0x44, 0x4C, 0x54, 0x5C))
    m0, m1, shift, qmin, qmax = (_reg(ip, o) for o in (0x64, 0x6C, 0x74, 0x7C, 0x84))
    # Same checks as eltwise_layer.cpp.
    if not (0 <= mode <= 2 and 1 <= ca <= 32 and 1 <= h <= 512 and 1 <= w <= 512):
        return 1
    if mode == 2:
        if h % 2 or w % 2:
            return 1
    elif not (0 <= shift <= 31 and -32768 <= qmin <= qmax <= 32767) or \
            (mode == 0 and cb != ca) or (mode == 1 and (ca % 8 or cb < 1 or ca + cb > 32)):
        return 1
    a = _unpack(ip.buffer(0x18), ca)
    b = _unpack(ip.buffer(0x24), cb) if mode != elt_ref.MODE_MAXPOOL else None
    y = elt_ref.eltwise_reference(mode, a, b, m0, m1, shift, qmin, qmax)
    _store(ip.buffer(0x30), y)
    return 0


class SimDevice(object):
    def __init__(self):
        self.memory = {}
        self.next_addr = 0x7000_0000   # arbitrary, 4 KiB aligned, below 4 GiB like CMA
        self.ips = {'conv_layer_0': SimIP(self.memory, run_conv),
                    'eltwise_layer_0': SimIP(self.memory, run_eltwise)}

    def allocate(self, shape, dtype):
        buf = np.zeros(shape, dtype=dtype).view(SimBuffer)
        buf.physical_address = self.next_addr
        self.memory[self.next_addr] = buf
        self.next_addr += (buf.nbytes + 0xFFF) & ~0xFFF
        return buf

    def ip(self, name):
        return self.ips[name]
